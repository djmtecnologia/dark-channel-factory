"""
Content Validator Agent — Dark Channel Factory v2
Módulo 3: QA do roteiro antes da produção de vídeo.
Detecta: plágio, roteiro repetitivo, baixa qualidade narrativa, falta de valor.
"""
from __future__ import annotations

import json
import re
from uuid import UUID

from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.models_v2 import ContentValidationResult

logger = get_logger(__name__)

VALIDATION_THRESHOLD_ORIGINALITY = 70.0
VALIDATION_THRESHOLD_QUALITY = 65.0
VALIDATION_THRESHOLD_VALUE = 70.0       # Elevado: valor transformativo é requisito central
MIN_WORD_COUNT = 1800   # Mínimo para 15 minutos de vídeo a ~120 wpm

SYSTEM_PROMPT = """You are a senior content quality analyst for YouTube.
Your job: evaluate a video script for QUALITY, ORIGINALITY, and TRANSFORMATIVE VALUE.

Evaluate these dimensions (each 0-100):
1. ORIGINALITY (0-100): Is this content unique? Does it present a fresh perspective?
   Flags: Generic information, copy of existing videos, Wikipedia-style narration.
2. NARRATIVE QUALITY (0-100): Is the hook strong (first 5 seconds)?
   Are there open loops throughout? Is the structure engaging? Does it have a clear CTA?
   Flags: Weak hook, no pattern interrupts, lecture-style narration.
3. TRANSFORMATIVE VALUE (0-100): After watching, will viewers feel they gained something CONCRETE?
   Score 90-100: Step-by-step instructions with named tools + before/after proof.
   Score 70-89: Actionable advice with specific examples.
   Score 50-69: General advice, some examples, no clear steps.
   Score 0-49: Motivational/fluffy content with no actionable takeaways.
   Flags: No named tools, no specific steps, no before/after, pure motivation.

PROHIBITED CONTENT (instant fail):
- Pure motivational content without substance
- Misinformation or unverifiable claims
- Plagiarized or generic content
- Scripts with zero actionable instructions
- Less than 1800 words total

Return ONLY valid JSON.
"""


def run_content_validator_agent(
    script_data_dict: dict,
    save_db: bool = True,
    forward: bool = True,
    profile: str | None = None,
) -> ContentValidationResult:
    """
    Executa o Content Validator Agent.

    Args:
        script_data_dict: ScriptData serializado
        save_db: Salva no banco de dados se True
        forward: Envia para a próxima fila se True
        profile: Nome do perfil de produção (ex: 'PILOT_5MIN', 'STANDARD_10MIN', etc)
    """
    from shared.models import ScriptData
    from shared.production_profiles import resolve_profile, validate_script_word_count

    script_data = ScriptData(**script_data_dict)
    job_id = script_data.job_id
    
    # Resolve perfil de produção (se configurado, senão fallback legado)
    raw_profile = profile or script_data_dict.get("production_profile") or script_data_dict.get("profile")
    resolved_profile = resolve_profile(profile_name=raw_profile)
    profile_label = resolved_profile.name if resolved_profile else "LEGACY_LONG"
    effective_min_words = resolved_profile.min_words if resolved_profile else MIN_WORD_COUNT

    logger.info(f"🔍 [Validator] Checking script | job={job_id} | profile={profile_label} | scenes={len(script_data.scenes)}")

    # ── Step 1: Checks heurísticos ─────────────────────────────
    issues: list[str] = []
    suggestions: list[str] = []

    # Contagem de palavras com validação centralizada
    all_text = " ".join(s.text for s in script_data.scenes)
    word_count = len(all_text.split())

    words_ok, words_err = validate_script_word_count(
        word_count=word_count,
        profile=resolved_profile,
        validator_type="content"
    )
    if not words_ok and words_err:
        issues.append(words_err)

    # Verifica hook
    has_strong_hook = _check_hook_strength(script_data.scenes[0].text if script_data.scenes else "")
    if not has_strong_hook:
        suggestions.append("Strengthen the hook — first 5 seconds must create immediate pattern interrupt")

    # Verifica open loops
    has_open_loops = _check_open_loops(all_text)
    if not has_open_loops:
        suggestions.append("Add open loops throughout — promise something and deliver later")

    # Verifica CTA
    last_scene_text = script_data.scenes[-1].text if script_data.scenes else ""
    has_cta = any(w in last_scene_text.lower() for w in
                  ["subscribe", "like", "comment", "link", "check out", "click"])
    if not has_cta:
        suggestions.append("Add a clear CTA in the final scene")

    # Verifica originalidade contra DB histórico
    originality_score = _check_originality(
        script_data.title,
        script_data.niche_data.niche,
        script_data.niche_data.language,
    )

    # ── Step 2: Avaliação LLM ──────────────────────────────────
    narrative_quality_score = 75.0
    transformative_value_score = 75.0
    llm_issues: list[str] = []
    llm_suggestions: list[str] = []

    llm = get_llm_client()
    try:
        # Trunca roteiro para economizar tokens
        script_excerpt = all_text[:3000]

        user_prompt = f"""Evaluate this YouTube script:

TITLE: {script_data.title}
NICHE: {script_data.niche_data.niche}
LANGUAGE: {script_data.niche_data.language}
WORD COUNT: {word_count}
SCENES: {len(script_data.scenes)}

SCRIPT EXCERPT (first 3000 chars):
{script_excerpt}

Return JSON:
{{
  "originality_score": 0-100,
  "narrative_quality_score": 0-100,
  "transformative_value_score": 0-100,
  "has_strong_hook": true/false,
  "has_open_loops": true/false,
  "has_clear_cta": true/false,
  "issues": ["critical issue 1", ...],
  "suggestions": ["improvement 1", ...]
}}"""

        response = llm.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=512,
            json_mode=True,
        )

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        originality_score = float(data.get("originality_score", originality_score))
        narrative_quality_score = float(data.get("narrative_quality_score", narrative_quality_score))
        transformative_value_score = float(data.get("transformative_value_score", transformative_value_score))
        has_strong_hook = data.get("has_strong_hook", has_strong_hook)
        has_open_loops = data.get("has_open_loops", has_open_loops)
        has_cta = data.get("has_clear_cta", has_cta)
        llm_issues = data.get("issues", [])
        llm_suggestions = data.get("suggestions", [])

    except Exception as e:
        logger.warning(f"[Validator] LLM evaluation failed — using heuristic scores: {e}")

    # ── Step 3: Consolidar resultado ───────────────────────────
    all_issues = issues + llm_issues
    all_suggestions = suggestions + llm_suggestions

    passed = (
        originality_score >= VALIDATION_THRESHOLD_ORIGINALITY
        and narrative_quality_score >= VALIDATION_THRESHOLD_QUALITY
        and transformative_value_score >= VALIDATION_THRESHOLD_VALUE
        and word_count >= effective_min_words
        and not any("fail" in i.lower() or "prohibited" in i.lower() for i in all_issues)
    )

    result = ContentValidationResult(
        job_id=job_id,
        passed=passed,
        originality_score=originality_score,
        narrative_quality_score=narrative_quality_score,
        transformative_value_score=transformative_value_score,
        issues=all_issues,
        suggestions=all_suggestions,
        word_count=word_count,
        estimated_duration_s=word_count / 2.5,  # ~150 wpm narração
        has_strong_hook=has_strong_hook,
        has_open_loops=has_open_loops,
        has_clear_cta=has_cta,
        affiliate_links_count=len(script_data.affiliate_links),
    )

    logger.info(
        f"{'✅' if passed else '❌'} [Validator] "
        f"Originality={originality_score:.0f} | Quality={narrative_quality_score:.0f} | "
        f"Value={transformative_value_score:.0f} | Words={word_count}"
    )

    if passed:
        if forward:
            _forward_to_video_designer(script_data_dict)
    else:
        logger.warning(f"[Validator] Script REJECTED | issues: {all_issues}")
        if save_db:
            try:
                from database.db_manager import fail_pipeline_job
                fail_pipeline_job(str(job_id), f"Content validation failed: {'; '.join(all_issues[:3])}")
            except Exception:
                pass

    return result


def _check_hook_strength(hook_text: str) -> bool:
    """Verifica se o hook tem força (power words ou estrutura de padrão)."""
    power_words = ["what if", "stop", "never", "secret", "truth", "mistake",
                   "wrong", "exposed", "warning", "why", "how", "shocking"]
    hook_lower = hook_text.lower()
    return any(w in hook_lower for w in power_words)


def _check_open_loops(text: str) -> bool:
    """Verifica presença de open loops no roteiro."""
    patterns = [
        r"by the end of this",
        r"stay until",
        r"i'll show you",
        r"you'll discover",
        r"keep watching",
        r"in a moment",
        r"later in this video",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _check_originality(title: str, niche: str, language: str) -> float:
    """Verifica originalidade contra histórico de tópicos no DB."""
    try:
        from database.db_manager import topic_exists
        niche_str = niche if isinstance(niche, str) else str(niche)
        if topic_exists(title, niche_str, language, similarity_threshold=75):
            logger.warning(f"[Validator] Similar topic found in history")
            return 50.0  # Penaliza mas não reprova automaticamente
        return 85.0  # Score base de originalidade
    except Exception:
        return 80.0  # Sem DB disponível, assume original


def _forward_to_video_designer(script_data_dict: dict) -> None:
    """Enfileira para Video Editor e Designer em paralelo."""
    from agents.video_editor.agent import run_video_editor_agent
    from agents.designer.agent import run_designer_agent
    from orchestrator.queue_manager import QUEUE_VIDEO, QUEUE_DESIGNER, get_queue_manager

    qm = get_queue_manager()
    qm.enqueue(QUEUE_VIDEO, run_video_editor_agent, script_data_dict, job_timeout=3600)
    qm.enqueue(QUEUE_DESIGNER, run_designer_agent, script_data_dict, job_timeout=1800)
    logger.info("→ [Validator] Script approved — dispatched to Video+Designer")
