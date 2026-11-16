"""
AI Video Director Engine — Orquestrador Central (Sprint 2.4)
Integra NarrativeAnalyzer, VisualDirector e ScenePlanner em um fluxo único,
determinístico, validado e observável para gerar o Master AV Plan.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from agents.video_director.master_plan_models import (
    ExecutionMetadata,
    MasterAVPlan,
    StageExecutionMetrics,
)
from agents.video_director.models import (
    NarrativeAnalysisResult,
    ScenePlanResult,
    VisualBible,
    VisualDirectionResult,
    VisualLanguage,
)
from agents.video_director.narrative_analyzer import NarrativeAnalyzer
from agents.video_director.scene_planner import ScenePlanner
from agents.video_director.security import sanitize_secret
from agents.video_director.visual_director import VisualDirector
from shared.production_profiles import (
    PROFILES,
    ProductionProfileConfig,
    ProductionProfileType,
)


class AIVideoDirectorError(Exception):
    """Exceção estruturada do AI Video Director preservando a etapa responsável."""

    def __init__(
        self,
        stage: str,
        error_type: str,
        message: str,
        cause: Optional[Exception] = None,
    ) -> None:
        self.stage = stage
        self.error_type = error_type
        self.message = sanitize_secret(message)
        self.cause = cause
        super().__init__(f"[{self.stage}] {self.error_type}: {self.message}")

    def to_dict(self) -> Dict[str, str]:
        return {
            "stage": self.stage,
            "error_type": self.error_type,
            "message": self.message,
        }


class AIVideoDirector:
    """
    Orquestrador central da Fase 2 (AI Video Director).
    Coordena:
      1. NarrativeAnalyzer
      2. VisualDirector
      3. ScenePlanner
      4. Master AV Plan Validator
    """

    def __init__(
        self,
        narrative_analyzer: Optional[NarrativeAnalyzer] = None,
        visual_director: Optional[VisualDirector] = None,
        scene_planner: Optional[ScenePlanner] = None,
        llm_client: Any = None,
        timeout: float = 30.0,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or math.isnan(timeout) or math.isinf(timeout) or timeout <= 0:
            raise ValueError(f"timeout must be a finite positive number, got {timeout!r}")

        self.timeout = float(timeout)
        self._llm_client = llm_client

        # Instancia componentes padrão apenas se não injetados, compartilhando llm_client
        if narrative_analyzer is None or visual_director is None or scene_planner is None:
            shared_client = self._resolve_llm_client()
        else:
            shared_client = self._llm_client

        self.narrative_analyzer = (
            narrative_analyzer
            if narrative_analyzer is not None
            else NarrativeAnalyzer(llm_client=shared_client, timeout=self.timeout)
        )
        self.visual_director = (
            visual_director
            if visual_director is not None
            else VisualDirector(llm_client=shared_client, timeout=self.timeout)
        )
        self.scene_planner = (
            scene_planner
            if scene_planner is not None
            else ScenePlanner(llm_client=shared_client, timeout=self.timeout)
        )

    def _resolve_llm_client(self) -> Any:
        if self._llm_client is not None:
            return self._llm_client
        from shared.llm_client import get_llm_client
        self._llm_client = get_llm_client()
        return self._llm_client

    @staticmethod
    def _resolve_profile_name(
        production_profile: Union[ProductionProfileType, ProductionProfileConfig, str],
    ) -> str:
        if isinstance(production_profile, ProductionProfileConfig):
            return production_profile.profile_type.value
        if isinstance(production_profile, ProductionProfileType):
            return production_profile.value
        if isinstance(production_profile, str):
            key = production_profile.strip().upper()
            if key in PROFILES:
                return key
        raise AIVideoDirectorError(
            stage="input_validation",
            error_type="InvalidProductionProfileError",
            message=f"Invalid or unsupported production_profile: {production_profile!r}",
        )

    @staticmethod
    def _extract_original_scenes(script_data: Any) -> List[Dict[str, Any]]:
        """Extrai lista normalizada de cenas originais (id, narration) sem mutar script_data."""
        if script_data is None:
            raise AIVideoDirectorError(
                stage="input_validation",
                error_type="EmptyScriptError",
                message="script_data cannot be None",
            )

        raw_scenes: Any = None
        if isinstance(script_data, list):
            raw_scenes = script_data
        elif isinstance(script_data, dict):
            raw_scenes = script_data.get("scenes")
        elif hasattr(script_data, "scenes"):
            raw_scenes = getattr(script_data, "scenes")
        else:
            raise AIVideoDirectorError(
                stage="input_validation",
                error_type="InvalidScriptInputError",
                message=f"Unsupported script_data type: {type(script_data).__name__}",
            )

        if not raw_scenes or not isinstance(raw_scenes, list):
            raise AIVideoDirectorError(
                stage="input_validation",
                error_type="EmptyScriptError",
                message="script_data must contain a non-empty list of scenes",
            )

        extracted: List[Dict[str, Any]] = []
        seen_ids = set()
        for idx, item in enumerate(raw_scenes):
            if isinstance(item, dict):
                scene_id = item.get("id") if item.get("id") is not None else item.get("scene_id")
                narration = item.get("narration") if item.get("narration") is not None else item.get("text")
            elif hasattr(item, "id") or hasattr(item, "scene_id"):
                scene_id = getattr(item, "id", None)
                if scene_id is None:
                    scene_id = getattr(item, "scene_id", None)
                narration = getattr(item, "narration", None)
                if narration is None:
                    narration = getattr(item, "text", None)
            else:
                raise AIVideoDirectorError(
                    stage="input_validation",
                    error_type="InvalidSceneError",
                    message=f"Scene at index {idx} has unsupported type {type(item).__name__}",
                )

            if scene_id is None or (isinstance(scene_id, str) and not scene_id.strip()):
                raise AIVideoDirectorError(
                    stage="input_validation",
                    error_type="InvalidSceneIdError",
                    message=f"Scene at index {idx} is missing a valid id/scene_id",
                )
            if scene_id in seen_ids:
                raise AIVideoDirectorError(
                    stage="input_validation",
                    error_type="DuplicateSceneIdError",
                    message=f"Duplicate scene_id in input script_data: {scene_id!r}",
                )
            seen_ids.add(scene_id)

            if not isinstance(narration, str) or not narration.strip():
                raise AIVideoDirectorError(
                    stage="input_validation",
                    error_type="EmptyNarrationError",
                    message=f"Scene {scene_id!r} has empty or invalid narration",
                )

            extracted.append({"id": scene_id, "narration": narration})

        return extracted

    def _run_with_timeout(self, stage: str, fn: Any, *args: Any, **kwargs: Any) -> Tuple[Any, float]:
        """Executa uma etapa diretamente com relógio monotônico e verificação de timeout.

        O timeout efetivo de I/O é delegado aos próprios componentes (via ``timeout=self.timeout``
        propagado aos clientes LLM e ``asyncio.wait_for``). Executar diretamente na thread atual
        evita threads abandonadas em segundo plano caso um timeout ocorra.
        """
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except AIVideoDirectorError:
            raise
        except (TimeoutError, concurrent.futures.TimeoutError) as exc:
            raise AIVideoDirectorError(
                stage=stage,
                error_type="TimeoutError",
                message=f"Stage '{stage}' exceeded timeout of {self.timeout:.2f}s",
                cause=exc,
            ) from exc
        except Exception as exc:
            err_msg = str(exc) or f"Stage '{stage}' failed"
            err_lower = err_msg.lower()
            if "timeout" in err_lower or "timed out" in err_lower:
                raise AIVideoDirectorError(
                    stage=stage,
                    error_type="TimeoutError",
                    message=err_msg,
                    cause=exc,
                ) from exc
            raise AIVideoDirectorError(
                stage=stage,
                error_type=type(exc).__name__,
                message=err_msg,
                cause=exc,
            ) from exc

        elapsed = time.monotonic() - start
        if elapsed > self.timeout:
            raise AIVideoDirectorError(
                stage=stage,
                error_type="TimeoutError",
                message=f"Stage '{stage}' exceeded timeout of {self.timeout:.2f}s (elapsed {elapsed:.2f}s)",
            )
        return result, elapsed

    @staticmethod
    def _validate_consolidated_plan(
        original_scenes: List[Dict[str, Any]],
        narrative_result: NarrativeAnalysisResult,
        visual_result: VisualDirectionResult,
        plan_result: ScenePlanResult,
        profile_name: str,
    ) -> None:
        """Validação consolidada de integridade do Master AV Plan."""
        if not isinstance(narrative_result, NarrativeAnalysisResult):
            raise AIVideoDirectorError(
                stage="narrative_analyzer",
                error_type="IncompleteResultError",
                message="NarrativeAnalyzer did not return a valid NarrativeAnalysisResult",
            )
        if not isinstance(visual_result, VisualDirectionResult):
            raise AIVideoDirectorError(
                stage="visual_director",
                error_type="IncompleteResultError",
                message="VisualDirector did not return a valid VisualDirectionResult",
            )
        if not isinstance(plan_result, ScenePlanResult):
            raise AIVideoDirectorError(
                stage="scene_planner",
                error_type="IncompleteResultError",
                message="ScenePlanner did not return a valid ScenePlanResult",
            )

        if visual_result.visual_bible is None or not isinstance(visual_result.visual_bible, VisualBible):
            raise AIVideoDirectorError(
                stage="master_plan_validation",
                error_type="MissingVisualBibleError",
                message="VisualBible is required and must be valid",
            )

        orig_count = len(original_scenes)
        narr_count = len(narrative_result.scene_analyses)
        vis_count = len(visual_result.scene_directions)
        plan_count = len(plan_result.scenes)

        if narr_count != orig_count:
            raise AIVideoDirectorError(
                stage="narrative_analyzer",
                error_type="SceneCountMismatchError",
                message=f"NarrativeAnalyzer returned {narr_count} scenes, expected {orig_count}",
            )
        if vis_count != narr_count:
            raise AIVideoDirectorError(
                stage="visual_director",
                error_type="SceneCountMismatchError",
                message=f"VisualDirector returned {vis_count} scenes, expected {narr_count}",
            )
        if plan_count != orig_count:
            raise AIVideoDirectorError(
                stage="scene_planner",
                error_type="SceneCountMismatchError",
                message=f"ScenePlanner returned {plan_count} scenes, expected {orig_count}",
            )

        orig_ids = [s["id"] for s in original_scenes]
        narr_ids = [s.scene_id for s in narrative_result.scene_analyses]
        vis_ids = [s.scene_id for s in visual_result.scene_directions]
        plan_ids = [s.scene_id for s in plan_result.scenes]

        if narr_ids != orig_ids:
            raise AIVideoDirectorError(
                stage="narrative_analyzer",
                error_type="SceneOrderOrIdMismatchError",
                message="NarrativeAnalyzer scene IDs or order do not match input scenes",
            )
        if vis_ids != orig_ids:
            raise AIVideoDirectorError(
                stage="visual_director",
                error_type="SceneOrderOrIdMismatchError",
                message="VisualDirector scene IDs or order do not match input scenes",
            )
        if plan_ids != orig_ids:
            raise AIVideoDirectorError(
                stage="scene_planner",
                error_type="SceneOrderOrIdMismatchError",
                message="ScenePlanner scene IDs or order do not match input scenes",
            )

        # Preservação integral da narração e validade das durações
        sum_durations = 0.0
        for orig, planned in zip(original_scenes, plan_result.scenes):
            if planned.narration != orig["narration"]:
                raise AIVideoDirectorError(
                    stage="master_plan_validation",
                    error_type="NarrationAlteredError",
                    message=f"Narration altered for scene {orig['id']!r}",
                )
            dur = planned.duration_seconds
            if isinstance(dur, bool) or not isinstance(dur, (int, float)) or math.isnan(dur) or math.isinf(dur) or dur <= 0:
                raise AIVideoDirectorError(
                    stage="master_plan_validation",
                    error_type="InvalidDurationError",
                    message=f"Invalid duration {dur!r} for scene {orig['id']!r}",
                )
            sum_durations += float(dur)

        tolerance = 1e-3 + 1e-9
        if abs(sum_durations - plan_result.total_duration_seconds) > tolerance:
            raise AIVideoDirectorError(
                stage="master_plan_validation",
                error_type="DurationSumMismatchError",
                message=(
                    f"Sum of scene durations ({sum_durations:.6f}) does not match "
                    f"total_duration_seconds ({plan_result.total_duration_seconds:.6f})"
                ),
            )

        if plan_result.profile != profile_name:
            raise AIVideoDirectorError(
                stage="master_plan_validation",
                error_type="ProfileMismatchError",
                message=f"ScenePlanResult profile {plan_result.profile!r} != {profile_name!r}",
            )

    def create_master_plan(
        self,
        script_data: Any,
        production_profile: Union[ProductionProfileType, ProductionProfileConfig, str] = "PILOT_5MIN",
        planning_params: Optional[Dict[str, Any]] = None,
        visual_language_override: Optional[VisualLanguage] = None,
    ) -> MasterAVPlan:
        """
        Executa o fluxo completo do AI Video Director e retorna um MasterAVPlan validado.
        Não muta script_data nem planning_params.
        """
        total_start = time.monotonic()

        # Cópias defensivas para garantir imutabilidade das entradas
        safe_script = copy.deepcopy(script_data)
        safe_params = copy.deepcopy(planning_params) if planning_params is not None else None

        profile_name = self._resolve_profile_name(production_profile)
        original_scenes = self._extract_original_scenes(safe_script)
        input_count = len(original_scenes)

        stages_metrics: List[StageExecutionMetrics] = []

        # Etapa 1: Narrative Analyzer
        narrative_result, narr_elapsed = self._run_with_timeout(
            "narrative_analyzer",
            self.narrative_analyzer.analyze,
            safe_script,
        )
        stages_metrics.append(
            StageExecutionMetrics(
                stage="narrative_analyzer",
                status="completed",
                duration_seconds=narr_elapsed,
                scene_count_in=input_count,
                scene_count_out=len(narrative_result.scene_analyses)
                if isinstance(narrative_result, NarrativeAnalysisResult)
                else 0,
            )
        )

        # Etapa 2: Visual Director
        visual_result, vis_elapsed = self._run_with_timeout(
            "visual_director",
            self.visual_director.direct,
            copy.deepcopy(narrative_result),
            visual_language_override=visual_language_override,
        )
        stages_metrics.append(
            StageExecutionMetrics(
                stage="visual_director",
                status="completed",
                duration_seconds=vis_elapsed,
                scene_count_in=len(narrative_result.scene_analyses)
                if isinstance(narrative_result, NarrativeAnalysisResult)
                else 0,
                scene_count_out=len(visual_result.scene_directions)
                if isinstance(visual_result, VisualDirectionResult)
                else 0,
            )
        )

        # Etapa 3: Scene Planner
        plan_result, plan_elapsed = self._run_with_timeout(
            "scene_planner",
            self.scene_planner.plan_scenes,
            copy.deepcopy(narrative_result),
            copy.deepcopy(visual_result),
            profile_name,
            original_scenes=copy.deepcopy(original_scenes),
            planning_params=safe_params,
        )
        stages_metrics.append(
            StageExecutionMetrics(
                stage="scene_planner",
                status="completed",
                duration_seconds=plan_elapsed,
                scene_count_in=len(visual_result.scene_directions)
                if isinstance(visual_result, VisualDirectionResult)
                else 0,
                scene_count_out=len(plan_result.scenes)
                if isinstance(plan_result, ScenePlanResult)
                else 0,
            )
        )

        # Etapa 4: Validação Consolidada do Master AV Plan
        val_start = time.monotonic()
        self._validate_consolidated_plan(
            original_scenes=original_scenes,
            narrative_result=narrative_result,
            visual_result=visual_result,
            plan_result=plan_result,
            profile_name=profile_name,
        )
        val_elapsed = time.monotonic() - val_start
        stages_metrics.append(
            StageExecutionMetrics(
                stage="master_plan_validation",
                status="completed",
                duration_seconds=val_elapsed,
                scene_count_in=len(plan_result.scenes),
                scene_count_out=len(plan_result.scenes),
            )
        )

        total_elapsed = time.monotonic() - total_start
        warnings = list(plan_result.validation_summary.get("warnings", []))

        execution_metadata = ExecutionMetadata(
            total_duration_seconds=total_elapsed,
            production_profile=profile_name,
            stages=stages_metrics,
            components={
                "narrative_analyzer": type(self.narrative_analyzer).__name__,
                "visual_director": type(self.visual_director).__name__,
                "scene_planner": type(self.scene_planner).__name__,
            },
            warnings=warnings,
        )

        try:
            master_plan = MasterAVPlan(
                production_profile=profile_name,
                central_theme=narrative_result.central_theme,
                visual_bible=copy.deepcopy(visual_result.visual_bible),
                total_scenes=len(plan_result.scenes),
                total_duration_seconds=plan_result.total_duration_seconds,
                scenes=copy.deepcopy(plan_result.scenes),
                validation_summary=copy.deepcopy(plan_result.validation_summary),
                execution_metadata=execution_metadata,
            )
            # Garante serialização JSON válida
            json_str = master_plan.model_dump_json()
            json.loads(json_str)
        except Exception as exc:
            raise AIVideoDirectorError(
                stage="master_plan_validation",
                error_type=type(exc).__name__,
                message=f"Failed to build or serialize MasterAVPlan: {exc}",
                cause=exc,
            ) from exc

        return master_plan
