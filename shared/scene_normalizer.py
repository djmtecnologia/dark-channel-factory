"""
Scene Normalization Layer — Dark Channel Factory (Contrato Canônico V2)
========================================================================
Normaliza objetos e dicionários de cena entre a Arquitetura A (Pydantic Scene)
e a Arquitetura B (dicionários operacionais do desktop_node, copywriter e retention_engine).

Diretrizes do Contrato Canônico V2:
1. Reconciliação id / scene_id bidirecional estrita com rejeição de conflitos e identificadores <= 0.
2. Reconciliação narration / text / content com prioridade determinística (sem mascarar ausência com string vazia).
3. Reconciliação duration_s / estimated_duration_s com rejeição estrita de zero, negativos, inf e NaN.
4. Geração determinística de b_roll_query curta (2 a 5 palavras) a partir de b_roll_visual com suporte multilíngue.
5. Normalização determinística de b_roll_query quando fornecida com mais de 5 palavras.
6. Geração de descrição visual coerente e explícita quando apenas b_roll_query for fornecida.
7. Preservação independente de b_roll_visual e b_roll_query quando ambos forem fornecidos.
8. Preservação estrita dos dicionários de entrada (imutabilidade profunda).
9. Validação e normalização de enums canônicos e aliases (ex: impact_micro_shake), rejeitando valores desconhecidos.
10. Validação de timestamps e consistência cronológica.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Union

# Enums canônicos e valores permitidos
VALID_CAMERA_FRAMINGS = {
    "extreme_wide", "wide", "medium", "close_up", "extreme_close_up", "macro"
}
VALID_CAMERA_MOVEMENTS = {
    "static", "slow_push_in", "dramatic_pull_out", "tracking_lateral",
    "crane_sweep", "orbit", "dutch_tilt", "impact_micro_shake"
}
VALID_NARRATIVE_PURPOSES = {
    "hook", "context", "tension", "technical_detail", "revelation",
    "historical_evidence", "transformation", "cta"
}
VALID_VISUAL_PRODUCTION_TYPES = {
    "auto", "local_asset", "stock_footage", "cinematic_still",
    "motion_graphic", "data_viz", "blender_3d", "ai_video"
}
VALID_SCENE_TYPES = {
    "hook", "problem", "agitation", "solution", "proof", "objection", "cta", "outro"
}
VALID_EMOTIONS = {
    "shock", "curiosity", "urgency", "trust", "excitement", "fear", "hope"
}

# Aliases legados suportados para normalização determinística
CAMERA_MOVEMENT_ALIASES = {
    "shake": "impact_micro_shake",
    "micro_shake": "impact_micro_shake",
    "impact_shake": "impact_micro_shake",
    "push_in": "slow_push_in",
    "pull_out": "dramatic_pull_out",
    "zoom_in": "slow_push_in",
    "zoom_out": "dramatic_pull_out",
    "tracking": "tracking_lateral",
    "crane": "crane_sweep",
    "tilt": "dutch_tilt",
}

# Stop words multilíngues (Inglês e Português) e ruídos de direção
STOP_WORDS = {
    # Inglês
    "a", "an", "the", "and", "or", "but", "if", "while", "of", "at", "by", "for", "with",
    "about", "against", "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "can", "will", "just", "should", "now",
    "cinematic", "hyperrealistic", "photorealistic", "ultra", "detailed", "lighting",
    "background", "shot", "footage", "4k", "8k", "hd", "macro", "extreme", "close", "up",
    "wide", "view", "scene", "showing", "depicting", "textured", "depth", "field",
    "authentic", "vintage", "atmosphere",
    # Português
    "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "pelo", "pela", "pelos", "pelas", "com", "para",
    "como", "que", "e", "ou", "mas", "se", "seu", "sua", "seus", "suas", "este", "esta",
    "estes", "estas", "esse", "essa", "esses", "essas", "aquele", "aquela", "aqueles", "aquelas",
    "muito", "mais", "menos", "onde", "quando", "quem", "qual", "quais", "cena", "mostrando",
    "fundo", "iluminacao", "detalhes", "foto", "video", "imagem", "filmagem"
}


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def extract_concise_broll_query(visual_desc: str, min_words: int = 2, max_words: int = 5) -> str:
    """
    Extrai deterministicamente uma query concisa de 2 a 5 palavras a partir de uma descrição visual.
    Suporta descrições em inglês e português, preservando termos conceituais, técnicos e numéricos.
    Quando a entrada possui menos de 2 palavras significativas, preserva a palavra sem inventar termos.
    """
    if not visual_desc or not str(visual_desc).strip():
        return ""

    text = str(visual_desc).strip()

    # Remove prefixos diretivos e cinematográficos comuns (EN + PT)
    prefix_pattern = (
        r"^(cinematic\s+|dramatic\s+|extreme\s+|close[- ]up\s+|wide\s+shot\s+|shot\s+of\s+|"
        r"video\s+of\s+|photo\s+of\s+|footage\s+of\s+|macro\s+shot\s+of\s+|establishing\s+shot\s+of\s+|"
        r"aerial\s+view\s+of\s+|plano\s+aberto\s+de\s+|cena\s+de\s+|foto\s+de\s+|video\s+de\s+|"
        r"filmagem\s+de\s+|vista\s+aerea\s+de\s+)+"
    )
    cleaned = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()
    if not cleaned:
        cleaned = text

    # Extrai tokens alfanuméricos preservando números e termos técnicos
    tokens = re.findall(r"[A-Za-z0-9]+(?:'[a-z]+)?", cleaned)
    if not tokens:
        tokens = re.findall(r"[A-Za-z0-9]+", text)
    if not tokens:
        return ""

    # Se a descrição limpa já tiver entre min_words e max_words palavras sem prefixos
    if min_words <= len(tokens) <= max_words and not re.search(r"^(cinematic|shot|footage|cena|video)", text, re.IGNORECASE):
        return " ".join(tokens)

    meaningful = [t for t in tokens if t.lower() not in STOP_WORDS and len(t) > 1]

    if len(meaningful) >= min_words:
        chosen = meaningful[:max_words]
    elif len(meaningful) > 0:
        # Preenche com outros tokens da frase que não sejam puramente numéricos isolados se necessário
        chosen = list(meaningful)
        for t in tokens:
            if t not in chosen and len(t) > 1 and t.lower() not in {"a", "o", "the", "and", "de", "in"}:
                chosen.append(t)
            if len(chosen) >= min_words:
                break
        chosen = chosen[:max_words]
    else:
        chosen = tokens[:max_words]

    return " ".join(chosen)


def normalize_broll_query(query: str, min_words: int = 2, max_words: int = 5) -> str:
    """
    Normaliza deterministicamente uma query de busca de B-roll.
    Se a query contiver mais de max_words palavras, extrai os termos semanticamente
    mais relevantes preservando o significado sem cortes indiscriminados.
    """
    if not query or not str(query).strip():
        return ""
    text = str(query).strip()
    words = text.split()
    if len(words) <= max_words:
        return text

    # Query extensa: aplica extração concisa determinística
    extracted = extract_concise_broll_query(text, min_words=min_words, max_words=max_words)
    return extracted if extracted else " ".join(words[:max_words])


def _parse_timestamp_range(ts: str) -> Optional[tuple[float, float]]:
    """Extrai os segundos de início e término de um timestamp 'M:SS-M:SS' ou 'SS-SS'."""
    if not ts or not isinstance(ts, str):
        return None
    ts_clean = ts.replace("s", "").strip()
    if "-" not in ts_clean:
        return None
    try:
        parts = ts_clean.split("-", 1)
        def _to_sec(t_str: str) -> float:
            t_str = t_str.strip()
            if ":" in t_str:
                m, s = t_str.split(":", 1)
                return int(m) * 60 + float(s)
            return float(t_str)
        t_start = _to_sec(parts[0])
        t_end = _to_sec(parts[1])
        return (t_start, t_end)
    except Exception:
        return None


def _validate_timestamp_format(ts: str) -> bool:
    """Valida se o formato do timestamp é cronologicamente válido (start <= end)."""
    parsed = _parse_timestamp_range(ts)
    if parsed is None:
        # Formatos pontuais sem hífen são tolerados se numéricos
        return bool(ts and isinstance(ts, str) and not re.search(r"[^\d:.\s]", ts.replace("s", "")))
    return parsed[0] <= parsed[1]


def normalize_scene(
    scene: Union[Dict[str, Any], Any],
    fallback_topic: Optional[str] = None,
    default_scene_index: int = 0,
    concise_query: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Normaliza um dicionário de cena ou modelo Pydantic para o contrato universal V2.

    Garante:
    - Imutabilidade profunda do dicionário original passado como entrada.
    - Sincronização e validação estrita entre 'id' e 'scene_id' (sem tolerar conflitos nem id <= 0).
    - Sincronização entre 'narration' e 'text' com prioridade determinística (rejeitando narração vazia).
    - Validação numérica de duração (rejeitando zero, negativos, inf e NaN).
    - Preservação independente de 'b_roll_visual' e 'b_roll_query' quando ambos existem.
    - Geração de query concisa (2 a 5 palavras) a partir de 'b_roll_visual' em modo V2 / concise_query.
    - Normalização determinística de query extensa (> 5 palavras).
    - Geração explícita de descrição visual quando apenas 'b_roll_query' for fornecida.
    - Validação e normalização de enums com rejeição de valores desconhecidos.
    - Preservação segura de metadados de asset e chaves customizadas.
    """
    # 0. Imutabilidade estrita: cópia profunda inicial incondicional
    if hasattr(scene, "model_dump"):
        raw = copy.deepcopy(scene.model_dump())
    elif hasattr(scene, "dict"):
        raw = copy.deepcopy(scene.dict())
    elif isinstance(scene, dict):
        raw = copy.deepcopy(scene)
    else:
        raw = copy.deepcopy(getattr(scene, "__dict__", {}))

    normalized: Dict[str, Any] = dict(raw)

    # 1. Identificação da cena: reconciliação e validação estrita
    raw_id = raw.get("id")
    raw_scene_id = raw.get("scene_id")

    if raw_id is not None and raw_scene_id is not None:
        try:
            id_int = int(raw_id)
            scene_id_int = int(raw_scene_id)
        except (ValueError, TypeError):
            raise ValueError(f"Scene identifiers must be valid integers: id={raw_id}, scene_id={raw_scene_id}")
        if id_int != scene_id_int:
            raise ValueError(f"Conflicting scene identifiers provided: id={raw_id} vs scene_id={raw_scene_id}")
        final_id = id_int
    elif raw_id is not None:
        try:
            final_id = int(raw_id)
        except (ValueError, TypeError):
            final_id = default_scene_index + 1
    elif raw_scene_id is not None:
        try:
            final_id = int(raw_scene_id)
        except (ValueError, TypeError):
            final_id = default_scene_index + 1
    else:
        final_id = default_scene_index + 1

    if final_id < 0:
        raise ValueError(f"Scene identifier must be a non-negative integer >= 0, got {final_id}")

    normalized["id"] = final_id
    normalized["scene_id"] = final_id

    # 2. Narração e texto da cena: prioridade determinística e rejeição de vazio
    narr_val = _clean_str(raw.get("narration"))
    text_val = _clean_str(raw.get("text"))
    content_val = _clean_str(raw.get("content"))

    # Prioridade determinística: narration > text > content
    chosen_narr = narr_val or text_val or content_val
    if not chosen_narr:
        raise ValueError("Narration must contain valid content; cannot be empty or whitespace")

    normalized["narration"] = chosen_narr
    normalized["text"] = chosen_narr

    # 3. Timing: duration_s e estimated_duration_s
    dur_val = raw.get("duration_s")
    est_dur_val = raw.get("estimated_duration_s")

    if dur_val is not None and est_dur_val is not None:
        try:
            d1 = float(dur_val)
            d2 = float(est_dur_val)
        except (ValueError, TypeError):
            raise ValueError(f"Duration values must be numeric: duration_s={dur_val}, estimated_duration_s={est_dur_val}")
        if math.isnan(d1) or math.isnan(d2) or math.isinf(d1) or math.isinf(d2):
            raise ValueError("Duration cannot be NaN or infinite")
        if d1 <= 0.0 or d2 <= 0.0:
            raise ValueError(f"Duration must be greater than zero: duration_s={d1}, estimated_duration_s={d2}")
        if abs(d1 - d2) > 0.01:
            raise ValueError(f"Conflicting duration values provided: duration_s={dur_val} vs estimated_duration_s={est_dur_val}")
        candidate_dur = d1
    elif dur_val is not None:
        try:
            candidate_dur = float(dur_val)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid duration_s: {dur_val}")
    elif est_dur_val is not None:
        try:
            candidate_dur = float(est_dur_val)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid estimated_duration_s: {est_dur_val}")
    elif raw.get("duration") is not None:
        try:
            candidate_dur = float(raw.get("duration"))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid duration: {raw.get('duration')}")
    else:
        candidate_dur = None

    timestamp = raw.get("timestamp")

    if candidate_dur is not None:
        if math.isnan(candidate_dur) or math.isinf(candidate_dur):
            raise ValueError("Duration cannot be NaN or infinite")
        if candidate_dur <= 0.0:
            raise ValueError(f"Duration must be greater than zero, got {candidate_dur}")
        # Saturation threshold: mínimo 1.0 segundo
        normalized_dur = max(1.0, candidate_dur)
    elif timestamp:
        try:
            ts_str = str(timestamp).replace("s", "").strip()
            if "-" in ts_str:
                parts = ts_str.split("-")
                def _to_sec(t_str: str) -> float:
                    t_str = t_str.strip()
                    if ":" in t_str:
                        m, s = t_str.split(":", 1)
                        return int(m) * 60 + float(s)
                    return float(t_str)
                calc_dur = _to_sec(parts[1]) - _to_sec(parts[0])
                if calc_dur <= 0.0:
                    raise ValueError(f"Timestamp specifies non-positive duration: {timestamp}")
                normalized_dur = max(1.0, calc_dur)
            else:
                normalized_dur = 5.0
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            normalized_dur = 5.0
    else:
        normalized_dur = 5.0

    normalized["duration_s"] = normalized_dur
    normalized["estimated_duration_s"] = normalized_dur

    if not timestamp:
        start_sec = default_scene_index * 5.0
        end_sec = start_sec + normalized_dur
        m1, s1 = divmod(int(start_sec), 60)
        m2, s2 = divmod(int(end_sec), 60)
        normalized["timestamp"] = f"{m1}:{s1:02d}-{m2}:{s2:02d}"
    else:
        ts_str = str(timestamp).strip()
        if not _validate_timestamp_format(ts_str):
            raise ValueError(f"Invalid timestamp sequence or inversion: '{timestamp}'")
        normalized["timestamp"] = ts_str

    # 4. B-Roll Visual e B-Roll Query
    raw_canonical_vis = _clean_str(raw.get("b_roll_visual"))
    raw_query = _clean_str(raw.get("b_roll_query"))
    raw_alt = (
        _clean_str(raw.get("visual_description"))
        or _clean_str(raw.get("visual_prompt"))
        or _clean_str(raw.get("visual"))
    )

    # Detecta se é requisição V2 (explícita via concise_query ou via presença de campos canônicos V2)
    is_v2 = (
        concise_query is True
        or any(k in raw for k in (
            "camera_framing", "camera_movement", "narrative_purpose",
            "visual_production_type", "asset_metadata", "lighting_mood"
        ))
    )

    if raw_canonical_vis and not raw_query:
        final_visual = raw_canonical_vis
        if is_v2:
            final_query = extract_concise_broll_query(raw_canonical_vis)
        else:
            final_query = raw_canonical_vis
    elif raw_alt and not raw_query and not raw_canonical_vis:
        final_visual = raw_alt
        final_query = raw_alt
    elif raw_query and not raw_canonical_vis and not raw_alt:
        final_query = normalize_broll_query(raw_query) if is_v2 and len(raw_query.split()) > 5 else raw_query
        if is_v2:
            final_visual = f"Cinematic visual representation based on: {final_query}"
        else:
            final_visual = raw_query
    elif (raw_canonical_vis or raw_alt) and raw_query:
        vis = raw_canonical_vis or raw_alt
        final_visual = vis
        final_query = normalize_broll_query(raw_query) if is_v2 and len(raw_query.split()) > 5 else raw_query
    else:
        clean_topic = _clean_str(fallback_topic)
        if clean_topic:
            final_visual = clean_topic
            final_query = clean_topic
        else:
            final_visual = "cinematic documentary background visuals"
            final_query = "cinematic documentary background visuals"

    normalized["b_roll_visual"] = final_visual
    normalized["b_roll_query"] = final_query

    # 5. Visual cues / Micro cues
    cues = raw.get("visual_cues") or raw.get("micro_cues") or []
    if isinstance(cues, list):
        cues_copy = copy.deepcopy(cues)
        normalized["visual_cues"] = cues_copy
        normalized["micro_cues"] = cues_copy
    else:
        normalized["visual_cues"] = []
        normalized["micro_cues"] = []

    # 6. Campos Canônicos V2 e Validação de Enums
    raw_st = str(raw.get("scene_type") or raw.get("type") or "solution").lower().strip()
    if is_v2 and raw_st not in VALID_SCENE_TYPES:
        raise ValueError(f"Invalid scene_type: '{raw_st}'. Allowed: {sorted(VALID_SCENE_TYPES)}")
    normalized["scene_type"] = raw_st

    raw_np = str(raw.get("narrative_purpose") or "context").lower().strip()
    if is_v2 and raw_np not in VALID_NARRATIVE_PURPOSES:
        raise ValueError(f"Invalid narrative_purpose: '{raw_np}'. Allowed: {sorted(VALID_NARRATIVE_PURPOSES)}")
    normalized["narrative_purpose"] = raw_np

    raw_vpt = str(raw.get("visual_production_type") or "auto").lower().strip()
    if is_v2 and raw_vpt not in VALID_VISUAL_PRODUCTION_TYPES:
        raise ValueError(f"Invalid visual_production_type: '{raw_vpt}'. Allowed: {sorted(VALID_VISUAL_PRODUCTION_TYPES)}")
    normalized["visual_production_type"] = raw_vpt

    raw_cf = str(raw.get("camera_framing") or "wide").lower().strip()
    if is_v2 and raw_cf not in VALID_CAMERA_FRAMINGS:
        raise ValueError(f"Invalid camera_framing: '{raw_cf}'. Allowed: {sorted(VALID_CAMERA_FRAMINGS)}")
    normalized["camera_framing"] = raw_cf

    # Normalização de camera_movement com aliases (ex: impact_micro_shake)
    raw_cm = str(raw.get("camera_movement") or "slow_push_in").lower().strip()
    cm_mapped = CAMERA_MOVEMENT_ALIASES.get(raw_cm, raw_cm)
    if is_v2 and cm_mapped not in VALID_CAMERA_MOVEMENTS:
        raise ValueError(f"Invalid camera_movement: '{raw_cm}'. Allowed: {sorted(VALID_CAMERA_MOVEMENTS)}")
    normalized["camera_movement"] = cm_mapped

    raw_em = str(raw.get("emotion") or "curiosity").strip()
    if is_v2:
        em_lower = raw_em.lower()
        if em_lower in {"authority", "confidence"}:
            em_lower = "trust"
        if em_lower not in VALID_EMOTIONS:
            raise ValueError(f"Invalid emotion: '{raw_em}'. Allowed: {sorted(VALID_EMOTIONS)}")
        normalized["emotion"] = em_lower
    else:
        # Preserva valor legado da Fase 1
        normalized["emotion"] = raw_em

    normalized["lighting_mood"] = str(raw.get("lighting_mood") or "dark_cinematic")
    normalized["transition_in"] = str(raw.get("transition_in") or "direct_cut")
    normalized["transition_out"] = str(raw.get("transition_out") or "crossfade_slow")
    normalized["continuity_anchor"] = raw.get("continuity_anchor") or raw.get("anchor_id")
    normalized["sfx_cue"] = raw.get("sfx_cue") or "none"
    normalized["on_screen_text"] = raw.get("on_screen_text") or raw.get("overlay_text")

    meta = raw.get("asset_metadata")
    if hasattr(meta, "model_dump"):
        normalized["asset_metadata"] = copy.deepcopy(meta.model_dump())
    elif isinstance(meta, dict):
        normalized["asset_metadata"] = copy.deepcopy(meta)
    else:
        normalized["asset_metadata"] = None

    cost_tier = raw.get("estimated_cost_tier") if raw.get("estimated_cost_tier") is not None else raw.get("tier")
    try:
        normalized["estimated_cost_tier"] = max(1, min(5, int(cost_tier))) if cost_tier is not None else 1
    except (ValueError, TypeError):
        normalized["estimated_cost_tier"] = 1

    return normalized


def normalize_scene_v2(
    scene: Union[Dict[str, Any], Any],
    fallback_topic: Optional[str] = None,
    default_scene_index: int = 0,
) -> Dict[str, Any]:
    """
    Normaliza uma cena aplicando estritamente as regras do Contrato Canônico V2,
    incluindo extração concisa de queries para APIs de estoque.
    """
    return normalize_scene(
        scene,
        fallback_topic=fallback_topic,
        default_scene_index=default_scene_index,
        concise_query=True,
    )


def normalize_scenes(
    scenes: List[Any],
    fallback_topic: Optional[str] = None,
    concise_query: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """
    Normaliza uma lista de cenas, recalculando os timestamps sequenciais caso não fornecidos.
    Garante integridade e sequência cronológica contínua.
    """
    if not scenes:
        return []

    normalized_list: List[Dict[str, Any]] = []
    current_time_s = 0.0

    for idx, scene in enumerate(scenes):
        norm = normalize_scene(
            scene,
            fallback_topic=fallback_topic,
            default_scene_index=idx,
            concise_query=concise_query,
        )
        # Verifica se timestamp original estava presente tanto em dict quanto em Pydantic
        orig_ts = None
        if isinstance(scene, dict):
            orig_ts = scene.get("timestamp")
        elif hasattr(scene, "timestamp"):
            orig_ts = getattr(scene, "timestamp")

        if orig_ts:
            parsed = _parse_timestamp_range(str(orig_ts))
            if parsed is None or parsed[0] > parsed[1]:
                raise ValueError(f"Invalid timestamp sequence or inversion in scene {idx + 1}: '{orig_ts}'")
            t_start, t_end = parsed
            if t_start < current_time_s:
                raise ValueError(
                    f"Chronological inconsistency between scenes: scene {idx + 1} starts at {t_start:.1f}s, "
                    f"prior to previous scene end ({current_time_s:.1f}s)"
                )
            dur = max(1.0, t_end - t_start)
            norm["duration_s"] = dur
            norm["estimated_duration_s"] = dur
            norm["timestamp"] = str(orig_ts).strip()
            current_time_s = t_end
        else:
            dur = norm["duration_s"]
            start_m, start_s = divmod(int(current_time_s), 60)
            end_m, end_s = divmod(int(current_time_s + dur), 60)
            norm["timestamp"] = f"{start_m}:{start_s:02d}-{end_m}:{end_s:02d}"
            current_time_s += dur

        normalized_list.append(norm)

    return normalized_list


def normalize_scenes_v2(
    scenes: List[Any],
    fallback_topic: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Normaliza uma lista de cenas sob o Contrato Canônico V2.
    """
    return normalize_scenes(scenes, fallback_topic=fallback_topic, concise_query=True)
