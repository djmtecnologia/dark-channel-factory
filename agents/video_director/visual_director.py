"""
Visual Director — AI Video Director
Transforma a interpretação narrativa em decisões de direção visual cinematográfica
compatíveis com o Contrato Canônico V2.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from agents.video_director.models import (
    NarrativeAnalysisResult,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionResult,
    VisualLanguage,
)
from agents.video_director.security import sanitize_secret
from shared.logger import get_logger
from shared.models import (
    CameraFraming,
    CameraMovement,
    Scene,
    VisualProductionType,
)
from shared.scene_normalizer import (
    CAMERA_MOVEMENT_ALIASES,
    extract_concise_broll_query,
    normalize_broll_query,
)

logger = get_logger(__name__)


class VisualDirectionError(Exception):
    """Exceção levantada quando a direção visual falha ou produz dados inválidos."""
    pass


VISUAL_DIRECTOR_SYSTEM_PROMPT = """You are a master cinematic and documentary visual director (AI Video Director).
Your responsibility is to take a structured narrative analysis of a documentary script and generate precise, motivated visual direction for every scene.

CORE PRINCIPLES:
1. MOTIVATED DIRECTION: Framing, movement, and visual resources must be driven by the scene's dramatic context, tension, and factual requirements. Never apply random visual effects.
2. CONTINUITY & CONNECTION: Explain how each scene connects visually to the previous and next scene. Maintain continuity anchors where applicable.
3. CONFORMANCE TO CANONICAL V2 CONTRACT:
   - camera_framing: Must be one of ["extreme_wide", "wide", "medium", "close_up", "extreme_close_up", "macro"].
   - camera_movement: Must be one of ["static", "slow_push_in", "dramatic_pull_out", "tracking_lateral", "crane_sweep", "orbit", "dutch_tilt", "impact_micro_shake"].
   - visual_production_type: Must be one of ["auto", "local_asset", "stock_footage", "cinematic_still", "motion_graphic", "data_viz", "blender_3d", "ai_video"].
   - transitions (transition_in / transition_out): Must be one of ["direct_cut", "crossfade_slow", "whip_pan_whoosh", "glitch_impact", "fade_to_black"].
   - b_roll_visual: Highly detailed cinematic prompt in English describing composition, lighting, texture, and subject.
   - b_roll_query: Concise 2 to 5 words search query for stock media libraries (no generic fluff).
   - on_screen_text: Text overlay (maximum 3 words or null if not applicable).

Visual Language options:
["documentary_realistic", "cinematic", "historical", "investigative", "dramatic", "contemplative", "tense"].

For each scene:
- scene_id: Exact integer ID matching the input scene.
- narration: The EXACT narration text provided (must not be altered).
- what_viewer_sees: Exact description of what appears on the screen.
- dominant_element: The focal point dominating the visual composition.
- visual_production_type: Most appropriate visual asset type.
- camera_framing: Camera framing choice.
- camera_movement: Motivated camera movement.
- depth_of_field: e.g. "shallow", "deep_focus", "rack_focus".
- composition: e.g. "rule_of_thirds", "leading_lines", "central_symmetry".
- lighting_mood: e.g. "dark_cinematic", "high_contrast_dramatic", "cold_fluorescent", "natural_soft".
- atmosphere: Emotional/physical atmosphere of the shot.
- color_palette_notes: Specific color notes for this shot.
- transition_in: In-transition.
- transition_out: Out-transition.
- visual_connection_previous: How this shot transitions from previous context.
- visual_connection_next: How this shot leads into the next context.
- continuity_anchor: Null or identifier of continuity anchor.
- b_roll_visual: Detailed English prompt.
- b_roll_query: Concise 2 to 5 words query.
- sfx_cue: Sound effect cue ("none" if none).
- on_screen_text: Text overlay (max 3 words or null).
- estimated_cost_tier: Integer 1 to 5.

OUTPUT FORMAT:
Return strictly a valid JSON object matching this schema with 'visual_bible' and 'scene_directions' without any markdown formatting or commentary.
"""


class VisualDirector:
    """
    Componente do AI Video Director responsável por transformar a interpretação narrativa
    em decisões de direção visual estruturadas e compatíveis com o Contrato Canônico V2.
    """

    def __init__(self, llm_client: Any = None, timeout: float = 30.0) -> None:
        self.llm_client = llm_client
        self.timeout = timeout

    def _get_client(self) -> Any:
        if self.llm_client is not None:
            return self.llm_client
        from shared.llm_client import get_llm_client
        return get_llm_client()

    def direct(
        self,
        narrative_analysis: NarrativeAnalysisResult,
        visual_language_override: Optional[VisualLanguage] = None,
    ) -> VisualDirectionResult:
        """
        Gera a direção visual cinematográfica a partir da análise narrativa estruturada.

        Args:
            narrative_analysis: NarrativeAnalysisResult com as interpretações de cada cena.
            visual_language_override: Opcional para forçar uma linguagem visual específica.

        Returns:
            VisualDirectionResult contendo a Bíblia Visual e as direções por cena.

        Raises:
            VisualDirectionError: Caso o LLM falhe, gere JSON malformado ou viole o contrato V2.
            ValueError: Se a análise narrativa de entrada for nula ou não contiver cenas.
        """
        if narrative_analysis is None:
            raise ValueError("narrative_analysis cannot be None")

        # 1. Cópia defensiva profunda para garantir ausência de mutação dos objetos de entrada
        analysis_copy = copy.deepcopy(narrative_analysis)

        if not analysis_copy.scene_analyses:
            raise ValueError("narrative_analysis contains no scene analyses to direct")

        # 2. Prepara o contexto para o prompt do LLM
        scenes_context = []
        for s in analysis_copy.scene_analyses:
            scenes_context.append({
                "scene_id": s.scene_id,
                "narration": s.original_narration,
                "scene_intent": s.scene_intent,
                "narrative_purpose": str(s.narrative_purpose),
                "emotion": str(s.predominant_emotion),
                "emotional_intensity": s.emotional_intensity,
                "narrative_tension": s.narrative_tension,
                "dramatic_importance": s.dramatic_importance,
                "factual_assertions": s.factual_assertions,
                "narrative_inferences": s.narrative_inferences,
                "visual_proof_requirements": s.visual_proof_requirements,
                "forbidden_fabrications": s.forbidden_fabrications,
                "required_visual_elements": s.required_visual_elements,
                "evidence_needed": [str(e) for e in s.evidence_needed],
                "visual_impact_potential": str(s.visual_impact_potential),
            })

        user_prompt_data = {
            "central_theme": analysis_copy.central_theme,
            "historical_or_factual_context": analysis_copy.historical_or_factual_context,
            "narrative_objective": analysis_copy.narrative_objective,
            "dominant_tone": analysis_copy.dominant_tone,
            "narrative_progression": analysis_copy.narrative_progression,
            "requested_visual_language": (
                visual_language_override.value if visual_language_override else None
            ),
            "scenes": scenes_context,
        }

        user_prompt = (
            f"Generate a cinematic visual direction for this documentary ({len(scenes_context)} scenes).\n"
            f"Analysis context:\n{json.dumps(user_prompt_data, ensure_ascii=False, indent=2, default=str)}\n\n"
            "Produce the complete VisualDirectionResult JSON with 'visual_bible' and 'scene_directions'."
        )

        client = self._get_client()

        # 3. Execução da chamada ao LLM com timeout real e sanitização estrita de segredos
        try:
            if hasattr(client, "complete"):
                sig = inspect.signature(client.complete)
                kwargs: Dict[str, Any] = {
                    "system_prompt": VISUAL_DIRECTOR_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "temperature": 0.2,
                    "max_tokens": 8192,
                    "json_mode": True,
                }
                if "timeout" not in sig.parameters:
                    raise VisualDirectionError(
                        "Synchronous llm_client must accept a 'timeout' parameter to guarantee real timeout enforcement"
                    )
                kwargs["timeout"] = self.timeout

                response = client.complete(**kwargs)
                raw_text = response.text if hasattr(response, "text") else str(response)
            elif hasattr(client, "chat_completion"):
                sig = inspect.signature(client.chat_completion)
                chat_kwargs: Dict[str, Any] = {
                    "system_prompt": VISUAL_DIRECTOR_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "json_mode": True,
                    "max_tokens": 8192,
                    "task_type": "video",
                }
                if "timeout" not in sig.parameters:
                    raise VisualDirectionError(
                        "Synchronous llm_client must accept a 'timeout' parameter to guarantee real timeout enforcement"
                    )
                chat_kwargs["timeout"] = self.timeout

                response = client.chat_completion(**chat_kwargs)
                raw_text = response.text if hasattr(response, "text") else str(response)
            else:
                raise VisualDirectionError("Configured LLM client does not provide 'complete' or 'chat_completion'")
        except Exception as e:
            safe_err = sanitize_secret(str(e))
            logger.error(f"[VisualDirector] LLM call failed: {safe_err}")
            err_lower = str(e).lower()
            if "timeout" in err_lower or "timed out" in err_lower or isinstance(e, TimeoutError):
                raise VisualDirectionError(f"LLM communication error: request timed out after {self.timeout}s: {safe_err}") from e
            raise VisualDirectionError(f"LLM communication error: {safe_err}") from e

        # 4. Limpeza e reparo de JSON
        raw_text = raw_text.strip()
        from shared.ai_orchestrator import orchestrator
        cleaned_json = orchestrator._clean_json(raw_text)

        try:
            parsed_data = json.loads(cleaned_json)
        except json.JSONDecodeError as decode_err:
            safe_decode_err = sanitize_secret(str(decode_err))
            logger.error(f"[VisualDirector] Malformed JSON response: {safe_decode_err}")
            raise VisualDirectionError(f"Malformed JSON returned by LLM: {safe_decode_err}") from decode_err

        if not isinstance(parsed_data, dict):
            raise VisualDirectionError(f"Expected JSON object from LLM, got {type(parsed_data).__name__}")

        # 5. Validação da Bíblia Visual SEM mascarar erros com defaults (Correção 3)
        raw_bible = parsed_data.get("visual_bible")
        if raw_bible is None or not isinstance(raw_bible, dict):
            raise VisualDirectionError("LLM response is missing 'visual_bible' dictionary")

        raw_bible_dict = dict(raw_bible)
        if visual_language_override:
            raw_bible_dict["visual_language"] = visual_language_override.value
        elif "visual_language" in raw_bible_dict and isinstance(raw_bible_dict["visual_language"], str):
            raw_bible_dict["visual_language"] = raw_bible_dict["visual_language"].strip().lower()

        try:
            visual_bible = VisualBible(**raw_bible_dict)
        except ValidationError as bible_err:
            safe_bible_err = sanitize_secret(str(bible_err))
            logger.error(f"[VisualDirector] Visual bible validation failed: {safe_bible_err}")
            raise VisualDirectionError(f"Visual bible validation failed: {safe_bible_err}") from bible_err

        # 6. Validação das direções de cena e contagem
        raw_scene_dirs = parsed_data.get("scene_directions")
        if raw_scene_dirs is None or not isinstance(raw_scene_dirs, list) or len(raw_scene_dirs) == 0:
            raise VisualDirectionError("LLM response is missing 'scene_directions' list or returned an empty list")

        if len(raw_scene_dirs) != len(analysis_copy.scene_analyses):
            raise VisualDirectionError(
                f"Scene directions count mismatch: expected {len(analysis_copy.scene_analyses)}, got {len(raw_scene_dirs)}"
            )

        # 7. Validação estrita de identificadores, ordem e ausência de duplicidade (Correção 9)
        expected_ids = [s.scene_id for s in analysis_copy.scene_analyses]
        llm_scene_ids: List[int] = []
        for idx_pos, raw_dir in enumerate(raw_scene_dirs):
            if not isinstance(raw_dir, dict):
                raise VisualDirectionError(f"Scene direction entry {idx_pos} is not a dictionary")
            
            raw_id = raw_dir.get("scene_id") if "scene_id" in raw_dir else raw_dir.get("id")
            if raw_id is None:
                raise VisualDirectionError(f"Scene direction entry {idx_pos} is missing scene_id")
            try:
                parsed_id = int(raw_id)
            except (ValueError, TypeError):
                raise VisualDirectionError(f"Scene direction entry {idx_pos} has invalid non-integer scene_id: {raw_id}")
            llm_scene_ids.append(parsed_id)

        # Rejeita duplicatas
        if len(llm_scene_ids) != len(set(llm_scene_ids)):
            raise VisualDirectionError(f"LLM returned duplicate scene_ids in scene_directions: {llm_scene_ids}")

        # Rejeita divergência de IDs ou ordem
        if llm_scene_ids != expected_ids:
            raise VisualDirectionError(
                f"Scene direction ID or order mismatch: expected {expected_ids}, got {llm_scene_ids}"
            )

        # 8. Validação estrutural Python e coerção rigorosa ao Contrato Canônico V2
        validated_scene_directions: List[SceneVisualDirection] = []
        for orig_scene, raw_dir in zip(analysis_copy.scene_analyses, raw_scene_dirs):
            dir_dict = dict(raw_dir)

            # Invariante Sagrada: narração e ID da cena correspondem estritamente à entrada original
            dir_dict["scene_id"] = orig_scene.scene_id
            dir_dict["narration"] = orig_scene.original_narration

            # Preserva campos contextuais para conversão V2
            dir_dict["duration_s"] = orig_scene.duration_s
            dir_dict["timestamp"] = orig_scene.timestamp
            dir_dict["narrative_purpose"] = orig_scene.narrative_purpose
            dir_dict["emotion"] = orig_scene.predominant_emotion
            dir_dict["scene_type"] = orig_scene.scene_type
            dir_dict["extra_fields"] = orig_scene.extra_fields

            # Coerções seguras de Enums e Aliases
            framing_raw = str(dir_dict.get("camera_framing", "wide")).strip().lower()
            dir_dict["camera_framing"] = framing_raw

            movement_raw = str(dir_dict.get("camera_movement", "slow_push_in")).strip().lower()
            movement_canonical = CAMERA_MOVEMENT_ALIASES.get(movement_raw, movement_raw)
            dir_dict["camera_movement"] = movement_canonical

            prod_type_raw = str(dir_dict.get("visual_production_type", "auto")).strip().lower()
            dir_dict["visual_production_type"] = prod_type_raw

            # Normalização de b_roll_query (2 a 5 palavras)
            broll_q = dir_dict.get("b_roll_query")
            broll_v = dir_dict.get("b_roll_visual", "")
            if not broll_q and broll_v:
                dir_dict["b_roll_query"] = extract_concise_broll_query(str(broll_v))
            elif broll_q:
                clean_q = " ".join(str(broll_q).strip().split())
                word_count = len(clean_q.split())
                if word_count > 5:
                    dir_dict["b_roll_query"] = normalize_broll_query(clean_q)
                elif word_count < 2 and broll_v:
                    fallback_q = extract_concise_broll_query(str(broll_v))
                    if len(fallback_q.split()) >= 2:
                        dir_dict["b_roll_query"] = fallback_q
                    else:
                        dir_dict["b_roll_query"] = clean_q
                else:
                    dir_dict["b_roll_query"] = clean_q
            else:
                dir_dict["b_roll_query"] = extract_concise_broll_query(orig_scene.original_narration)

            if not broll_v:
                dir_dict["b_roll_visual"] = f"Cinematic documentary visual representing {dir_dict.get('b_roll_query', '')}"

            try:
                scene_dir = SceneVisualDirection(**dir_dict)
            except ValidationError as val_err:
                safe_val_err = sanitize_secret(str(val_err))
                logger.error(f"[VisualDirector] Scene {orig_scene.scene_id} validation failed: {safe_val_err}")
                raise VisualDirectionError(f"Validation failed for scene {orig_scene.scene_id}: {safe_val_err}") from val_err

            # Validação incondicional de igualdade textual estrita (Correção 1)
            if scene_dir.narration != orig_scene.original_narration:
                raise VisualDirectionError(
                    f"Narration corruption detected for scene {orig_scene.scene_id}: "
                    f"visual direction narration does not match original exactly."
                )

            validated_scene_directions.append(scene_dir)

        final_result = VisualDirectionResult(
            visual_bible=visual_bible,
            scene_directions=validated_scene_directions,
        )

        return final_result
