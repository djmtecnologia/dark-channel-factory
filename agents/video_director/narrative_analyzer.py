"""
Narrative Analyzer — AI Video Director
Interpreta o conteúdo narrativo do roteiro, diferenciando fatos, inferências,
necessidades de prova visual e intensidade dramática cena a cena.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from agents.video_director.models import (
    EvidenceType,
    NarrativeAnalysisResult,
    SceneNarrativeInterpretation,
    VisualImpactPotential,
)
from agents.video_director.security import sanitize_secret
from shared.logger import get_logger
from shared.models import Emotion, NarrativePurpose, Scene, ScriptData

logger = get_logger(__name__)


class NarrativeAnalysisError(Exception):
    """Exceção levantada quando a análise narrativa falha ou retorna dados inválidos."""
    pass


NARRATIVE_ANALYZER_SYSTEM_PROMPT = """You are a master narrative and dramatic analyst for world-class documentary cinema.
Your responsibility is to analyze a documentary script scene-by-scene and extract deep dramatic, factual, and visual-evidential structures.

CRITICAL EPISTEMIC SEPARATION:
1. FACTUAL ASSERTIONS (factual_assertions): Only what the narration explicitly states as historical or empirical fact.
2. NARRATIVE INFERENCES (narrative_inferences): Contextual and psychological deductions that explain subtext, clearly demarcated as inferences. Must NEVER duplicate factual assertions.
3. VISUAL PROOF REQUIREMENTS (visual_proof_requirements): Specific documents, telemetry, archival footage, maps, or physical objects needed to visually corroborate the narrative.
4. FORBIDDEN FABRICATIONS (forbidden_fabrications): Specific things that MUST NOT be visually or factually invented (e.g., anachronistic technology, fake documents, unproven actions).

For each scene:
- scene_id: Exact integer ID matching the input scene.
- original_narration: The EXACT narration text provided (must not be altered).
- scene_intent: The dramatic/narrative intention of this section.
- narrative_purpose: One of ["hook", "context", "tension", "technical_detail", "revelation", "historical_evidence", "transformation", "cta"].
- predominant_emotion: One of ["shock", "curiosity", "urgency", "trust", "excitement", "fear", "hope"].
- emotional_intensity: Float between 0.0 (minimal) and 1.0 (extreme).
- narrative_tension: Float between 0.0 (calm) and 1.0 (peak suspense/conflict).
- dramatic_importance: Float between 0.0 and 1.0 (weight in the overall story arc).
- factual_assertions: List of factual claims made.
- narrative_inferences: List of valid dramatic inferences.
- visual_proof_requirements: List of items needing visual proof.
- forbidden_fabrications: List of things prohibited from being fabricated.
- required_visual_elements: Indispensable visual elements for audience comprehension.
- evidence_needed: Categories of evidence required from ["document", "map", "photograph", "person", "location", "object", "data_metric", "telemetry"].
- visual_impact_potential: One of ["low", "medium", "high", "very_high"].

Global Analysis:
- central_theme: Core topic and thesis of the documentary.
- historical_or_factual_context: Historical era, setting, or factual context.
- narrative_objective: What the documentary aims to reveal, prove, or transform.
- narrative_progression: Ordered list of 3 to 6 narrative milestone steps (no duplicates, no empty items).
- dominant_tone: Dominant narrative tone (e.g., "historical_investigative", "dramatic_noir").

OUTPUT FORMAT:
Return strictly a valid JSON object matching this schema without any markdown formatting or commentary.
"""


class NarrativeAnalyzer:
    """
    Componente do AI Video Director responsável pela interpretação dramática e semântica de roteiros.
    Preserva estritamente o roteiro original e valida a saída com Pydantic.
    """

    def __init__(self, llm_client: Any = None, timeout: float = 30.0) -> None:
        self.llm_client = llm_client
        self.timeout = timeout

    def _get_client(self) -> Any:
        if self.llm_client is not None:
            return self.llm_client
        from shared.llm_client import get_llm_client
        return get_llm_client()

    @staticmethod
    def _extract_scenes_from_input(script_input: Any) -> List[Dict[str, Any]]:
        """
        Extrai a lista de cenas e narrações a partir de múltiplos formatos de entrada aceitos.
        PRESERVA A NARRAÇÃO ORIGINAL RIGOROSAMENTE SEM APLICAR .strip() OU MODIFICAÇÕES.
        """
        if script_input is None:
            raise ValueError("script_input cannot be None")

        if isinstance(script_input, ScriptData):
            scenes = []
            for s in script_input.scenes:
                narr_str = str(s.narration)
                if not narr_str.strip():
                    raise ValueError(f"Scene {s.id} has empty narration")
                scenes.append({
                    "id": s.id,
                    "narration": narr_str,
                    "duration_s": getattr(s, "duration_s", None) or getattr(s, "estimated_duration_s", None),
                    "timestamp": getattr(s, "timestamp", None),
                    "scene_type": getattr(s, "scene_type", None),
                    "extra_fields": getattr(s, "extra_fields", {}),
                })
            return scenes

        if isinstance(script_input, dict):
            raw_scenes = script_input.get("scenes") or script_input.get("Cenas") or []
            if not raw_scenes and ("narration" in script_input or "text" in script_input):
                raw_scenes = [script_input]

            scenes = []
            for idx, s in enumerate(raw_scenes, start=1):
                if isinstance(s, dict):
                    sid = s.get("id") or s.get("scene_id") or idx
                    if "narration" in s:
                        narr = s.get("narration")
                    elif "text" in s:
                        narr = s.get("text")
                    elif "content" in s:
                        narr = s.get("content")
                    else:
                        raise ValueError(f"Scene {sid} has missing narration")
                    
                    if narr is None:
                        raise ValueError(f"Scene {sid} has missing narration")
                    narr_str = str(narr)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {sid} has empty narration")
                    
                    dur = s.get("duration_s") or s.get("estimated_duration_s") or s.get("duration")
                    ts = s.get("timestamp")
                    st = s.get("scene_type") or s.get("type")
                    extra = {k: v for k, v in s.items() if k not in {"id", "scene_id", "narration", "text", "content", "duration_s", "estimated_duration_s", "duration", "timestamp", "scene_type", "type"}}

                    scenes.append({
                        "id": int(sid),
                        "narration": narr_str,
                        "duration_s": float(dur) if dur is not None else None,
                        "timestamp": str(ts) if ts is not None else None,
                        "scene_type": st,
                        "extra_fields": extra,
                    })
                elif isinstance(s, str):
                    narr_str = str(s)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {idx} has empty narration")
                    scenes.append({
                        "id": idx,
                        "narration": narr_str,
                        "duration_s": None,
                        "timestamp": None,
                        "scene_type": None,
                        "extra_fields": {},
                    })
                elif isinstance(s, Scene):
                    narr_str = str(s.narration)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {s.id} has empty narration")
                    scenes.append({
                        "id": s.id,
                        "narration": narr_str,
                        "duration_s": s.duration_s,
                        "timestamp": s.timestamp,
                        "scene_type": s.scene_type,
                        "extra_fields": {},
                    })
            return scenes

        if isinstance(script_input, list):
            scenes = []
            for idx, s in enumerate(script_input, start=1):
                if isinstance(s, dict):
                    sid = s.get("id") or s.get("scene_id") or idx
                    if "narration" in s:
                        narr = s.get("narration")
                    elif "text" in s:
                        narr = s.get("text")
                    elif "content" in s:
                        narr = s.get("content")
                    else:
                        raise ValueError(f"Scene {sid} has missing narration")
                    
                    if narr is None:
                        raise ValueError(f"Scene {sid} has missing narration")
                    narr_str = str(narr)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {sid} has empty narration")
                    
                    dur = s.get("duration_s") or s.get("estimated_duration_s") or s.get("duration")
                    ts = s.get("timestamp")
                    st = s.get("scene_type") or s.get("type")
                    extra = {k: v for k, v in s.items() if k not in {"id", "scene_id", "narration", "text", "content", "duration_s", "estimated_duration_s", "duration", "timestamp", "scene_type", "type"}}

                    scenes.append({
                        "id": int(sid),
                        "narration": narr_str,
                        "duration_s": float(dur) if dur is not None else None,
                        "timestamp": str(ts) if ts is not None else None,
                        "scene_type": st,
                        "extra_fields": extra,
                    })
                elif isinstance(s, Scene):
                    narr_str = str(s.narration)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {s.id} has empty narration")
                    scenes.append({
                        "id": s.id,
                        "narration": narr_str,
                        "duration_s": s.duration_s,
                        "timestamp": s.timestamp,
                        "scene_type": s.scene_type,
                        "extra_fields": {},
                    })
                elif isinstance(s, str):
                    narr_str = str(s)
                    if not narr_str.strip():
                        raise ValueError(f"Scene {idx} has empty narration")
                    scenes.append({
                        "id": idx,
                        "narration": narr_str,
                        "duration_s": None,
                        "timestamp": None,
                        "scene_type": None,
                        "extra_fields": {},
                    })
            return scenes

        raise ValueError(f"Unsupported script_input type: {type(script_input)}")

    def analyze(self, script_input: Union[ScriptData, Dict[str, Any], List[Any]]) -> NarrativeAnalysisResult:
        """
        Interpreta o conteúdo narrativo do roteiro.
        
        Args:
            script_input: ScriptData, dicionário de roteiro ou lista de cenas.
            
        Returns:
            NarrativeAnalysisResult estruturado e validado.
            
        Raises:
            NarrativeAnalysisError: Caso o LLM falhe, gere JSON malformado ou viole o contrato.
            ValueError: Se o roteiro de entrada for vazio ou inválido.
        """
        # 1. Cópia defensiva imediata para garantir imutabilidade estrita da entrada
        defensive_input = copy.deepcopy(script_input)
        raw_scenes = self._extract_scenes_from_input(defensive_input)

        if not raw_scenes:
            raise ValueError("Script contains no scenes to analyze")

        # Verifica se todas as cenas possuem narração válida
        for s in raw_scenes:
            if not s["narration"] or not s["narration"].strip():
                raise ValueError(f"Scene {s['id']} has empty narration")

        total_words = sum(len(s["narration"].split()) for s in raw_scenes)

        # 2. Constrói o prompt para o LLM
        scenes_prompt_data = [
            {"scene_id": s["id"], "narration": s["narration"]}
            for s in raw_scenes
        ]

        user_prompt = (
            f"Analyze the following documentary script containing {len(raw_scenes)} scenes ({total_words} total words).\n"
            f"Scenes to analyze:\n{json.dumps(scenes_prompt_data, ensure_ascii=False, indent=2, default=str)}\n\n"
            "Return the complete JSON structured analysis."
        )

        client = self._get_client()

        # 3. Execução da chamada LLM com propagação real de timeout e proteção estrita de segredos
        try:
            if hasattr(client, "complete"):
                sig = inspect.signature(client.complete)
                kwargs: Dict[str, Any] = {
                    "system_prompt": NARRATIVE_ANALYZER_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "temperature": 0.2,
                    "max_tokens": 8192,
                    "json_mode": True,
                }
                if "timeout" not in sig.parameters:
                    raise NarrativeAnalysisError(
                        "Synchronous llm_client must accept a 'timeout' parameter to guarantee real timeout enforcement"
                    )
                kwargs["timeout"] = self.timeout

                response = client.complete(**kwargs)
                raw_text = response.text if hasattr(response, "text") else str(response)
            elif hasattr(client, "chat_completion"):
                sig = inspect.signature(client.chat_completion)
                chat_kwargs: Dict[str, Any] = {
                    "system_prompt": NARRATIVE_ANALYZER_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "json_mode": True,
                    "max_tokens": 8192,
                    "task_type": "video",
                }
                if "timeout" not in sig.parameters:
                    raise NarrativeAnalysisError(
                        "Synchronous llm_client must accept a 'timeout' parameter to guarantee real timeout enforcement"
                    )
                chat_kwargs["timeout"] = self.timeout

                response = client.chat_completion(**chat_kwargs)
                raw_text = response.text if hasattr(response, "text") else str(response)
            else:
                raise NarrativeAnalysisError("Configured LLM client does not provide 'complete' or 'chat_completion'")
        except Exception as e:
            safe_err = sanitize_secret(str(e))
            logger.error(f"[NarrativeAnalyzer] LLM call failed: {safe_err}")
            err_lower = str(e).lower()
            if "timeout" in err_lower or "timed out" in err_lower or isinstance(e, TimeoutError):
                raise NarrativeAnalysisError(f"LLM communication error: request timed out after {self.timeout}s: {safe_err}") from e
            raise NarrativeAnalysisError(f"LLM communication error: {safe_err}") from e

        # 4. Limpeza e reparo de JSON (usando o orquestrador padrão do projeto)
        raw_text = raw_text.strip()
        from shared.ai_orchestrator import orchestrator
        cleaned_json = orchestrator._clean_json(raw_text)

        try:
            parsed_data = json.loads(cleaned_json)
        except json.JSONDecodeError as decode_err:
            safe_decode_err = sanitize_secret(str(decode_err))
            logger.error(f"[NarrativeAnalyzer] Malformed JSON response: {safe_decode_err}")
            raise NarrativeAnalysisError(f"Malformed JSON returned by LLM: {safe_decode_err}") from decode_err

        if not isinstance(parsed_data, dict):
            raise NarrativeAnalysisError(f"Expected JSON object from LLM, got {type(parsed_data).__name__}")

        # 5. Validação estrutural com Pydantic e preservação incondicional do roteiro original
        raw_scene_analyses = parsed_data.get("scene_analyses")
        if raw_scene_analyses is None or not isinstance(raw_scene_analyses, list) or len(raw_scene_analyses) == 0:
            raise NarrativeAnalysisError("LLM response is missing 'scene_analyses' list or returned an empty list")

        if len(raw_scene_analyses) != len(raw_scenes):
            raise NarrativeAnalysisError(
                f"Scene count mismatch: expected {len(raw_scenes)} scenes, got {len(raw_scene_analyses)}"
            )

        # 6. Validação estrita de identificadores, ordem e ausência de duplicidade (Correção 9)
        expected_ids = [s["id"] for s in raw_scenes]
        llm_scene_ids: List[int] = []
        for idx_pos, raw_ana in enumerate(raw_scene_analyses):
            if not isinstance(raw_ana, dict):
                raise NarrativeAnalysisError(f"Scene analysis item {idx_pos} is not a dictionary")
            
            raw_id = raw_ana.get("scene_id") if "scene_id" in raw_ana else raw_ana.get("id")
            if raw_id is None:
                raise NarrativeAnalysisError(f"Scene analysis item {idx_pos} is missing scene_id")
            try:
                parsed_id = int(raw_id)
            except (ValueError, TypeError):
                raise NarrativeAnalysisError(f"Scene analysis item {idx_pos} has invalid non-integer scene_id: {raw_id}")
            llm_scene_ids.append(parsed_id)

        # Rejeita IDs duplicados
        if len(llm_scene_ids) != len(set(llm_scene_ids)):
            raise NarrativeAnalysisError(f"LLM returned duplicate scene_ids: {llm_scene_ids}")

        # Rejeita divergência de IDs ou ordem
        if llm_scene_ids != expected_ids:
            raise NarrativeAnalysisError(
                f"Scene ID or order mismatch: expected {expected_ids}, got {llm_scene_ids}"
            )

        # Mapeamento e reconciliação determinística com o roteiro de entrada
        reconciled_scenes: List[SceneNarrativeInterpretation] = []
        for orig_s, raw_ana in zip(raw_scenes, raw_scene_analyses):
            ana_copy = dict(raw_ana)
            # Invariante Sagrada: o texto da narração e o ID da cena NUNCA podem ser alterados pelo LLM
            ana_copy["scene_id"] = orig_s["id"]
            ana_copy["original_narration"] = orig_s["narration"]

            # Preserva metadados de entrada para conversão canônica V2
            ana_copy["duration_s"] = orig_s.get("duration_s")
            ana_copy["timestamp"] = orig_s.get("timestamp")
            ana_copy["scene_type"] = orig_s.get("scene_type")
            ana_copy["extra_fields"] = orig_s.get("extra_fields", {})

            # Tratamento de enum coerções seguras
            if "narrative_purpose" in ana_copy and isinstance(ana_copy["narrative_purpose"], str):
                ana_copy["narrative_purpose"] = ana_copy["narrative_purpose"].strip().lower()
            if "predominant_emotion" in ana_copy and isinstance(ana_copy["predominant_emotion"], str):
                emo_val = ana_copy["predominant_emotion"].strip().lower()
                if emo_val in {"authority", "confidence"}:
                    emo_val = "trust"
                ana_copy["predominant_emotion"] = emo_val

            try:
                scene_interp = SceneNarrativeInterpretation(**ana_copy)
            except ValidationError as val_err:
                safe_val_err = sanitize_secret(str(val_err))
                logger.error(f"[NarrativeAnalyzer] Scene {orig_s['id']} validation failed: {safe_val_err}")
                raise NarrativeAnalysisError(f"Validation failed for scene {orig_s['id']}: {safe_val_err}") from val_err

            # Validação incondicional de igualdade textual estrita (Correção 1)
            if scene_interp.original_narration != orig_s["narration"]:
                raise NarrativeAnalysisError(
                    f"Narration corruption detected for scene {orig_s['id']}: "
                    f"output narrative text does not match original exactly."
                )

            reconciled_scenes.append(scene_interp)

        # Constrói e valida o resultado global
        progression = parsed_data.get("narrative_progression")
        if progression is None:
            raise NarrativeAnalysisError("LLM response is missing 'narrative_progression'")

        result_dict = {
            "central_theme": parsed_data.get("central_theme", "Documentary Theme"),
            "historical_or_factual_context": parsed_data.get("historical_or_factual_context", "General Historical / Factual Context"),
            "narrative_objective": parsed_data.get("narrative_objective", "Documentary Narrative Objective"),
            "narrative_progression": progression,
            "dominant_tone": parsed_data.get("dominant_tone", "documentary_investigative"),
            "scene_analyses": reconciled_scenes,
            "raw_word_count": total_words,
            "total_scenes": len(reconciled_scenes),
        }

        try:
            return NarrativeAnalysisResult(**result_dict)
        except ValidationError as val_err:
            safe_val_err = sanitize_secret(str(val_err))
            logger.error(f"[NarrativeAnalyzer] Global analysis validation failed: {safe_val_err}")
            raise NarrativeAnalysisError(f"Global narrative analysis validation error: {safe_val_err}") from val_err
