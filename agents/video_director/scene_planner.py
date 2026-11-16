"""
Scene Planner — AI Video Director (Sprint 2.3)
Planejador cinematográfico e narrativo determinístico com suporte controlado de LLM.
Transforma NarrativeAnalysisResult + VisualDirectionResult + ProductionProfile
em ScenePlanResult compatível com o Contrato Canônico Scene V2.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from agents.video_director.models import (
    NarrativeAnalysisResult,
    PlannedScene,
    SceneNarrativeInterpretation,
    ScenePlanResult,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionResult,
)
from agents.video_director.security import sanitize_secret
from shared.logger import get_logger
from shared.models import NarrativePurpose
from shared.production_profiles import (
    PROFILES,
    ProductionProfileConfig,
    ProductionProfileType,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Exceções do ScenePlanner
# ─────────────────────────────────────────────────────────────

class ScenePlannerError(Exception):
    """Exceção base para erros do ScenePlanner."""
    pass


class ScenePlannerValidationError(ScenePlannerError):
    """Erro de validação estrutural, IDs, ordem, narração ou contrato V2."""
    pass


class DurationMismatchError(ScenePlannerError):
    """Erro quando as durações são inválidas (<=0, NaN, Inf) ou incompatíveis com o perfil."""
    pass


class IncompleteLLMResponseError(ScenePlannerError):
    """Erro quando o LLM retorna resposta incompleta, malformada ou com cenas inventadas/faltantes."""
    pass


class VisualContinuityError(ScenePlannerError):
    """Erro crítico de continuidade visual sem fallback determinístico possível."""
    pass


# ─────────────────────────────────────────────────────────────
# Mapeamentos Determinísticos
# ─────────────────────────────────────────────────────────────

NARRATIVE_PURPOSE_TO_ROLE: Dict[str, str] = {
    NarrativePurpose.HOOK.value: "hook",
    NarrativePurpose.CONTEXT.value: "context",
    NarrativePurpose.TENSION.value: "development",
    NarrativePurpose.TECHNICAL_DETAIL.value: "explanation",
    NarrativePurpose.REVELATION.value: "revelation",
    NarrativePurpose.HISTORICAL_EVIDENCE.value: "evidence",
    NarrativePurpose.TRANSFORMATION.value: "climax",
    NarrativePurpose.CTA.value: "conclusion",
}


def _format_timestamp_range(start_s: float, end_s: float) -> str:
    start_m, start_sec = divmod(int(round(start_s)), 60)
    end_m, end_sec = divmod(int(round(end_s)), 60)
    return f"{start_m}:{start_sec:02d}-{end_m}:{end_sec:02d}"


def _extract_scene_id(scene: Any) -> int:
    if isinstance(scene, dict):
        if "id" in scene and scene["id"] is not None:
            return int(scene["id"])
        if "scene_id" in scene and scene["scene_id"] is not None:
            return int(scene["scene_id"])
    else:
        sid = getattr(scene, "id", None)
        if sid is None:
            sid = getattr(scene, "scene_id", None)
        if sid is not None:
            return int(sid)
    raise ScenePlannerValidationError("Original scene is missing 'id' or 'scene_id'")


def _extract_narration(scene: Any) -> str:
    if isinstance(scene, dict):
        for key in ("narration", "text", "content", "original_narration"):
            if key in scene and scene[key] is not None:
                return str(scene[key])
    else:
        for attr in ("narration", "text", "content", "original_narration"):
            val = getattr(scene, attr, None)
            if val is not None:
                return str(val)
    raise ScenePlannerValidationError("Original scene is missing narration text")


def _extract_explicit_duration(scene: Any) -> Any:
    if isinstance(scene, dict):
        for key in ("duration_s", "estimated_duration_s", "duration"):
            if key in scene and scene[key] is not None:
                return scene[key]
    else:
        for attr in ("duration_s", "estimated_duration_s", "duration"):
            val = getattr(scene, attr, None)
            if val is not None:
                return val
    return None


def _validate_duration_value(val: Any, scene_id: int) -> float:
    if isinstance(val, bool):
        raise DurationMismatchError(f"Invalid duration type for scene {scene_id}: {val!r}")
    if isinstance(val, (int, float)):
        fval = float(val)
    elif isinstance(val, str):
        try:
            fval = float(val.strip())
        except ValueError as exc:
            raise DurationMismatchError(
                f"Invalid duration string for scene {scene_id}: {val!r}"
            ) from exc
    else:
        raise DurationMismatchError(f"Invalid duration type for scene {scene_id}: {val!r}")

    if math.isnan(fval) or math.isinf(fval) or fval < 1.0:
        raise DurationMismatchError(
            f"Duration for scene {scene_id} must be finite and >= 1.0s, got {val!r}"
        )
    return fval


class ScenePlanner:
    """
    Planejador Cinematográfico Inteligente (Sprint 2.3).
    Combina regras determinísticas rigorosas em Python com suporte opcional e controlado de LLM.
    """

    def __init__(
        self,
        llm_client: Any = None,
        timeout: float = 30.0,
        fallback_on_llm_error: bool = False,
    ) -> None:
        self.llm_client = llm_client
        self.timeout = timeout
        self.fallback_on_llm_error = fallback_on_llm_error

    def _resolve_profile(
        self,
        production_profile: Union[ProductionProfileType, ProductionProfileConfig, str],
    ) -> ProductionProfileConfig:
        if isinstance(production_profile, ProductionProfileConfig):
            return production_profile
        if isinstance(production_profile, ProductionProfileType):
            return PROFILES[production_profile.value]
        if isinstance(production_profile, str):
            key = production_profile.strip().upper()
            if key in PROFILES:
                return PROFILES[key]
        raise ScenePlannerValidationError(
            f"Invalid or unsupported production_profile: {production_profile!r}"
        )

    def plan_scenes(
        self,
        narrative_analysis: NarrativeAnalysisResult,
        visual_direction: VisualDirectionResult,
        production_profile: Union[ProductionProfileType, ProductionProfileConfig, str],
        original_scenes: Optional[List[Any]] = None,
        planning_params: Optional[Dict[str, Any]] = None,
    ) -> ScenePlanResult:
        """
        Executa o planejamento cinematográfico completo das cenas.
        Não muta os objetos de entrada.
        """
        params = copy.deepcopy(planning_params) if planning_params else {}
        profile_cfg = self._resolve_profile(production_profile)

        # 1. Validação estrutural das entradas
        if narrative_analysis is None or not isinstance(narrative_analysis, NarrativeAnalysisResult):
            raise ScenePlannerValidationError("narrative_analysis must be a valid NarrativeAnalysisResult")
        if visual_direction is None or not isinstance(visual_direction, VisualDirectionResult):
            raise ScenePlannerValidationError("visual_direction must be a valid VisualDirectionResult")
        if visual_direction.visual_bible is None or not isinstance(visual_direction.visual_bible, VisualBible):
            raise ScenePlannerValidationError("visual_direction.visual_bible is required and must be valid")

        analyses = copy.deepcopy(narrative_analysis.scene_analyses)
        directions = copy.deepcopy(visual_direction.scene_directions)
        visual_bible = copy.deepcopy(visual_direction.visual_bible)

        if not analyses:
            raise ScenePlannerValidationError("narrative_analysis.scene_analyses cannot be empty")
        if not directions:
            raise ScenePlannerValidationError("visual_direction.scene_directions cannot be empty")
        if len(analyses) != len(directions):
            raise ScenePlannerValidationError(
                f"Scene count mismatch: narrative_analysis has {len(analyses)} scenes, "
                f"visual_direction has {len(directions)} scenes"
            )

        # 2. Validação de IDs únicos, ordem e correspondência
        seen_ids = set()
        analysis_ids: List[int] = []
        for a in analyses:
            if a.scene_id in seen_ids:
                raise ScenePlannerValidationError(f"Duplicate scene_id {a.scene_id} in narrative_analysis")
            seen_ids.add(a.scene_id)
            analysis_ids.append(a.scene_id)

        dir_seen = set()
        direction_ids: List[int] = []
        for d in directions:
            if d.scene_id in dir_seen:
                raise ScenePlannerValidationError(f"Duplicate scene_id {d.scene_id} in visual_direction")
            dir_seen.add(d.scene_id)
            direction_ids.append(d.scene_id)

        if analysis_ids != direction_ids:
            if set(analysis_ids) != set(direction_ids):
                raise ScenePlannerValidationError(
                    f"Inconsistent scene_ids between narrative_analysis {analysis_ids} "
                    f"and visual_direction {direction_ids}"
                )
            raise ScenePlannerValidationError(
                f"Scenes out of order between narrative_analysis {analysis_ids} "
                f"and visual_direction {direction_ids}"
            )

        orig_map: Dict[int, Any] = {}
        if original_scenes is not None:
            if not original_scenes:
                raise ScenePlannerValidationError("original_scenes cannot be an empty list when provided")
            orig_ids: List[int] = []
            for s in original_scenes:
                sid = _extract_scene_id(s)
                if sid in orig_map:
                    raise ScenePlannerValidationError(f"Duplicate scene_id {sid} in original_scenes")
                orig_map[sid] = copy.deepcopy(s)
                orig_ids.append(sid)
            if orig_ids != analysis_ids:
                if set(orig_ids) != set(analysis_ids):
                    raise ScenePlannerValidationError(
                        f"Scene IDs in original_scenes {orig_ids} do not match analyses {analysis_ids}"
                    )
                raise ScenePlannerValidationError(
                    f"original_scenes order {orig_ids} does not match expected order {analysis_ids}"
                )

        # 3. Preservação estrita da narração original (byte-a-byte)
        for a, d in zip(analyses, directions):
            if a.original_narration != d.narration:
                raise ScenePlannerValidationError(
                    f"Narration mismatch for scene {a.scene_id}: "
                    f"NarrativeAnalyzer and VisualDirector narrations differ"
                )
            if a.scene_id in orig_map:
                orig_narr = _extract_narration(orig_map[a.scene_id])
                if orig_narr != a.original_narration:
                    raise ScenePlannerValidationError(
                        f"Narration altered for scene {a.scene_id}: does not match original_scenes verbatim"
                    )

        # 4. Planejamento determinístico de durações
        durations = self._plan_durations(analyses, directions, orig_map, profile_cfg, params)

        # 5. Planejamento determinístico de continuidade, papéis e transições
        llm_suggestions: Dict[int, Dict[str, Any]] = {}
        use_llm = params.get("use_llm", True)
        if self.llm_client is not None and use_llm:
            llm_suggestions = self._consult_llm(analyses, directions, visual_bible, profile_cfg)

        planned_scenes: List[PlannedScene] = []
        current_time = 0.0
        prev_framing: Optional[str] = None
        prev_lighting: Optional[str] = None
        prev_query: Optional[str] = None
        consecutive_framing_count = 0

        for idx, (a, d, dur) in enumerate(zip(analyses, directions, durations), start=1):
            start_time = round(current_time, 3)
            end_time = round(current_time + dur, 3)
            ts_str = _format_timestamp_range(start_time, end_time)
            current_time = end_time

            # Papel narrativo
            purpose_val = a.narrative_purpose.value if isinstance(a.narrative_purpose, NarrativePurpose) else str(a.narrative_purpose)
            default_role = NARRATIVE_PURPOSE_TO_ROLE.get(purpose_val, "development")
            llm_scene = llm_suggestions.get(a.scene_id, {})
            narrative_role = str(llm_scene.get("narrative_role") or default_role).strip()

            # Continuidade visual: distinguir repetição intencional vs acidental
            framing = str(d.camera_framing)
            if framing == prev_framing:
                consecutive_framing_count += 1
            else:
                consecutive_framing_count = 1

            lighting_changed = prev_lighting is not None and str(d.lighting_mood) != prev_lighting
            query_repeated = prev_query is not None and d.b_roll_query.lower() == prev_query.lower()

            intentional_repetition = bool(
                consecutive_framing_count >= 2 and (d.continuity_anchor or d.visual_connection_previous)
            )
            accidental_framing_redundancy = consecutive_framing_count >= 3 and not intentional_repetition
            accidental_query_redundancy = query_repeated and not bool(d.continuity_anchor)

            variation_recommendation: Optional[str] = None
            structured_warnings: List[str] = []
            if accidental_framing_redundancy:
                variation_recommendation = (
                    f"Consider varying camera_framing from '{framing}' (repeated {consecutive_framing_count}x "
                    f"without continuity_anchor/visual_connection_previous) while preserving VisualBible '{visual_bible.visual_language}'."
                )
                structured_warnings.append(
                    f"accidental_framing_repetition:scene={a.scene_id}:framing={framing}:count={consecutive_framing_count}"
                )
            if accidental_query_redundancy:
                structured_warnings.append(
                    f"accidental_query_repetition:scene={a.scene_id}:query={d.b_roll_query}"
                )
            if lighting_changed and not (d.visual_connection_previous or d.transition_in != "direct_cut"):
                structured_warnings.append(
                    f"unjustified_lighting_shift:scene={a.scene_id}:lighting={d.lighting_mood}"
                )

            continuity_notes: Dict[str, Any] = {
                "continuity_anchor": d.continuity_anchor,
                "visual_connection_previous": d.visual_connection_previous,
                "visual_connection_next": d.visual_connection_next,
                "lighting_mood": d.lighting_mood,
                "camera_framing": framing,
                "camera_movement": str(d.camera_movement),
                "consecutive_framing_count": consecutive_framing_count,
                "intentional_repetition": intentional_repetition,
                "framing_repetition_warning": accidental_framing_redundancy,
                "query_repetition_warning": accidental_query_redundancy,
                "lighting_shift_justified": bool(
                    not lighting_changed or d.visual_connection_previous or d.transition_in != "direct_cut"
                ),
                "variation_recommendation": variation_recommendation,
                "structured_warnings": structured_warnings,
                "visual_bible_language": str(visual_bible.visual_language),
            }

            transition_in = str(llm_scene.get("transition_in") or d.transition_in or "direct_cut")
            transition_out = str(llm_scene.get("transition_out") or d.transition_out or "crossfade_slow")
            transition_rationale = str(
                llm_scene.get("transition_rationale")
                or self._build_transition_rationale(idx, len(analyses), transition_in, lighting_changed, purpose_val)
            )
            planning_rationale = str(
                llm_scene.get("planning_rationale")
                or f"Scene {a.scene_id} serves as '{narrative_role}' ({purpose_val}) with {dur:.2f}s allocated."
            )

            extra_fields: Dict[str, Any] = {}
            if a.scene_id in orig_map:
                orig_s = orig_map[a.scene_id]
                if isinstance(orig_s, dict) and "timestamp" in orig_s and orig_s["timestamp"] is not None:
                    extra_fields["original_timestamp"] = orig_s["timestamp"]
                elif not isinstance(orig_s, dict) and getattr(orig_s, "timestamp", None) is not None:
                    extra_fields["original_timestamp"] = getattr(orig_s, "timestamp")

            planned_scenes.append(
                PlannedScene(
                    scene_id=a.scene_id,
                    order=idx,
                    narration=a.original_narration,
                    narrative_role=narrative_role,
                    narrative_purpose=a.narrative_purpose,
                    visual_intent=d.what_viewer_sees,
                    duration_seconds=dur,
                    start_time=start_time,
                    end_time=end_time,
                    timestamp=ts_str,
                    visual_direction=d,
                    transition_in=transition_in,
                    transition_out=transition_out,
                    transition_rationale=transition_rationale,
                    continuity_notes=continuity_notes,
                    planning_rationale=planning_rationale,
                    extra_fields=extra_fields,
                )
            )

            prev_framing = framing
            prev_lighting = str(d.lighting_mood)
            prev_query = d.b_roll_query

        total_duration = round(sum(ps.duration_seconds for ps in planned_scenes), 3)
        scene_count = len(planned_scenes)
        scene_count_within_range = (
            profile_cfg.min_scenes <= scene_count <= profile_cfg.max_scenes
        )
        profile_scene_count_warning: Optional[str] = None
        if not scene_count_within_range:
            profile_scene_count_warning = (
                f"Scene count ({scene_count}) is outside recommended range "
                f"[{profile_cfg.min_scenes}, {profile_cfg.max_scenes}] for profile '{profile_cfg.name}'."
            )
            logger.warning(profile_scene_count_warning)

        all_continuity_warnings = [
            w
            for ps in planned_scenes
            for w in ps.continuity_notes.get("structured_warnings", [])
        ]

        durations_valid_and_finite = all(
            not (math.isnan(ps.duration_seconds) or math.isinf(ps.duration_seconds))
            and ps.duration_seconds >= 1.0
            for ps in planned_scenes
        )
        tolerance = float(params.get("duration_tolerance_s", 0.0))
        min_allowed = profile_cfg.min_duration_s - tolerance
        max_allowed = profile_cfg.max_duration_s + tolerance
        profile_bounds_respected = min_allowed <= total_duration <= max_allowed

        return ScenePlanResult(
            profile=profile_cfg.name,
            total_duration_seconds=total_duration,
            scene_count=scene_count,
            scenes=planned_scenes,
            visual_bible=visual_bible,
            validation_summary={
                "narration_preserved_verbatim": True,
                "ids_unique_and_ordered": True,
                "durations_valid_and_finite": durations_valid_and_finite,
                "profile_bounds_respected": profile_bounds_respected,
                "enforce_profile_duration": bool(params.get("enforce_profile_duration", True)),
                "duration_tolerance_s": tolerance,
                "scene_count_within_profile_range": scene_count_within_range,
                "profile_scene_count_warning": profile_scene_count_warning,
                "continuity_warnings_count": len(all_continuity_warnings),
                "continuity_warnings": all_continuity_warnings,
                "v2_contract_compatible": True,
            },
            metadata={
                "central_theme": narrative_analysis.central_theme,
                "dominant_tone": narrative_analysis.dominant_tone,
                "target_width": profile_cfg.target_width,
                "target_height": profile_cfg.target_height,
            },
        )

    def _plan_durations(
        self,
        analyses: List[SceneNarrativeInterpretation],
        directions: List[SceneVisualDirection],
        orig_map: Dict[int, Any],
        profile_cfg: ProductionProfileConfig,
        params: Dict[str, Any],
    ) -> List[float]:
        explicit_durations: List[Optional[float]] = []
        for a, d in zip(analyses, directions):
            dur: Optional[float] = None
            if a.scene_id in orig_map:
                dur = _extract_explicit_duration(orig_map[a.scene_id])
            if dur is None and d.duration_s is not None:
                dur = float(d.duration_s)
            if dur is None and a.duration_s is not None:
                dur = float(a.duration_s)

            if dur is not None:
                dur = _validate_duration_value(dur, a.scene_id)
            explicit_durations.append(dur)

        # Se todas as cenas têm duração explícita, preserva exatamente e valida contra o perfil
        if all(d is not None for d in explicit_durations):
            durations = [float(d) for d in explicit_durations if d is not None]
        else:
            # Distribui o tempo restante proporcionalmente ao peso narrativo e complexidade
            target_total = float(
                params.get(
                    "target_duration_s",
                    (profile_cfg.min_duration_s + profile_cfg.max_duration_s) / 2.0,
                )
            )
            fixed_sum = sum(d for d in explicit_durations if d is not None)
            remaining_indices = [i for i, d in enumerate(explicit_durations) if d is None]
            remaining_budget = target_total - fixed_sum

            if remaining_budget < len(remaining_indices) * 1.0:
                raise DurationMismatchError(
                    f"Insufficient remaining duration ({remaining_budget:.2f}s) for "
                    f"{len(remaining_indices)} unplanned scenes (minimum 1.0s per scene)"
                )

            weights: List[float] = []
            for i in remaining_indices:
                a = analyses[i]
                word_count = max(1, len(a.original_narration.split()))
                weight = (0.5 + a.dramatic_importance + 0.5 * a.emotional_intensity) * (1.0 + word_count / 25.0)
                weights.append(weight)

            total_weight = sum(weights)
            durations = [0.0] * len(analyses)
            for i, d in enumerate(explicit_durations):
                if d is not None:
                    durations[i] = d

            # Distribuir o orçamento excedente acima do mínimo de 1.0s por cena
            # garante que nenhuma cena fique < 1.0s e evita erro de soma por clamping.
            excess_budget = remaining_budget - len(remaining_indices) * 1.0
            allocated_sum = 0.0
            for idx_pos, scene_idx in enumerate(remaining_indices):
                if idx_pos == len(remaining_indices) - 1:
                    val = round(remaining_budget - allocated_sum, 3)
                    durations[scene_idx] = max(1.0, val)
                else:
                    share = 1.0 + excess_budget * (weights[idx_pos] / total_weight)
                    val = round(share, 3)
                    durations[scene_idx] = val
                    allocated_sum += val

        total_duration = round(sum(durations), 3)
        enforce_bounds = params.get("enforce_profile_duration", True)
        tolerance = float(params.get("duration_tolerance_s", 0.0))

        if enforce_bounds:
            min_allowed = profile_cfg.min_duration_s - tolerance
            max_allowed = profile_cfg.max_duration_s + tolerance
            if total_duration < min_allowed or total_duration > max_allowed:
                raise DurationMismatchError(
                    f"Total planned duration ({total_duration:.2f}s) is incompatible with profile "
                    f"'{profile_cfg.name}' [{profile_cfg.min_duration_s:.1f}s - {profile_cfg.max_duration_s:.1f}s]"
                )

        return durations

    def _build_transition_rationale(
        self,
        order: int,
        total_scenes: int,
        transition_in: str,
        lighting_changed: bool,
        purpose_val: str,
    ) -> str:
        if order == 1:
            return f"Opening scene ({purpose_val}) using {transition_in}."
        if lighting_changed:
            return f"Transition '{transition_in}' bridges lighting/atmosphere shift into {purpose_val}."
        return f"Narrative progression to {purpose_val} via {transition_in} (scene {order}/{total_scenes})."

    def _consult_llm(
        self,
        analyses: List[SceneNarrativeInterpretation],
        directions: List[SceneVisualDirection],
        visual_bible: VisualBible,
        profile_cfg: ProductionProfileConfig,
    ) -> Dict[int, Dict[str, Any]]:
        """Consulta opcional e controlada ao LLM para refinamento qualitativo."""
        payload = {
            "profile": profile_cfg.name,
            "visual_language": str(visual_bible.visual_language),
            "scenes": [
                {
                    "scene_id": a.scene_id,
                    "narrative_purpose": str(a.narrative_purpose),
                    "dramatic_importance": a.dramatic_importance,
                    "transition_in": d.transition_in,
                    "transition_out": d.transition_out,
                }
                for a, d in zip(analyses, directions)
            ],
        }
        prompt = (
            "Review the documentary scene progression and return JSON with key 'scenes' "
            "containing scene_id, narrative_role, transition_in, transition_out, transition_rationale, planning_rationale.\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            raw = self._invoke_llm_with_timeout(prompt)
            if isinstance(raw, str):
                data = json.loads(raw)
            elif isinstance(raw, dict):
                data = raw
            else:
                raise IncompleteLLMResponseError("LLM response must be a JSON string or dict")
        except IncompleteLLMResponseError:
            if self.fallback_on_llm_error:
                return {}
            raise
        except Exception as exc:
            safe_msg = sanitize_secret(str(exc))
            if self.fallback_on_llm_error:
                logger.warning(f"ScenePlanner LLM fallback activated: {safe_msg}")
                return {}
            raise IncompleteLLMResponseError(f"ScenePlanner LLM call failed: {safe_msg}") from exc

        if not isinstance(data, dict) or "scenes" not in data or not isinstance(data["scenes"], list):
            if self.fallback_on_llm_error:
                return {}
            raise IncompleteLLMResponseError("LLM response missing 'scenes' list")

        expected_ids = {a.scene_id for a in analyses}
        seen_ids = set()
        suggestions: Dict[int, Dict[str, Any]] = {}
        for item in data["scenes"]:
            if not isinstance(item, dict) or "scene_id" not in item:
                if self.fallback_on_llm_error:
                    return {}
                raise IncompleteLLMResponseError("LLM scene entry missing 'scene_id'")
            try:
                sid = int(item["scene_id"])
            except (TypeError, ValueError) as exc:
                raise ScenePlannerValidationError(
                    f"LLM returned invalid scene_id: {item['scene_id']!r}"
                ) from exc

            if sid in seen_ids:
                raise ScenePlannerValidationError(f"LLM returned duplicate scene_id: {sid}")
            seen_ids.add(sid)

            if sid not in expected_ids:
                raise ScenePlannerValidationError(f"LLM invented unknown scene_id: {sid}")
            if "narration" in item:
                # Se o LLM devolveu narração, valida que não foi alterada
                orig = next(a.original_narration for a in analyses if a.scene_id == sid)
                if item["narration"] != orig:
                    raise ScenePlannerValidationError(f"LLM attempted to alter narration for scene {sid}")
            suggestions[sid] = item

        if len(data["scenes"]) != len(expected_ids) or set(suggestions.keys()) != expected_ids:
            if self.fallback_on_llm_error:
                return {}
            missing = sorted(expected_ids - set(suggestions.keys()))
            raise IncompleteLLMResponseError(f"LLM response missing scenes: {missing}")

        return suggestions

    def _invoke_llm_with_timeout(self, prompt: str) -> Any:
        client = self.llm_client
        if callable(client):
            fn = client
        elif hasattr(client, "generate"):
            fn = client.generate
        elif hasattr(client, "complete"):
            fn = client.complete
        else:
            raise IncompleteLLMResponseError("Unsupported llm_client interface")

        if inspect.iscoroutinefunction(fn):
            async def _run() -> Any:
                return await asyncio.wait_for(fn(prompt), timeout=self.timeout)
            try:
                return asyncio.run(_run())
            except asyncio.TimeoutError as exc:
                raise IncompleteLLMResponseError(
                    f"Async LLM call timed out after {self.timeout}s"
                ) from exc

        # Para chamadas síncronas, exige suporte explícito ao parâmetro timeout
        sig = inspect.signature(fn)
        if "timeout" in sig.parameters:
            return fn(prompt, timeout=self.timeout)

        raise IncompleteLLMResponseError(
            "Synchronous llm_client must accept a 'timeout' parameter to guarantee real timeout enforcement"
        )

