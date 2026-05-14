"""
Viral Score Agent — Dark Channel Factory v2
Módulo 2: Gatekeeper pré-produção. Calcula "Viral Score" 0-100.
Se score < 70, otimiza o tópico automaticamente (até 2 tentativas).
Se ainda < 70, aborta o job e loga no DB.
"""
from __future__ import annotations

import json
import re
from uuid import UUID

from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.models_v2 import ViralScoreBreakdown, ViralScoreResult

logger = get_logger(__name__)

# Threshold padrão — configurável via env
VIRAL_SCORE_THRESHOLD = 70.0
MAX_OPTIMIZATION_RETRIES = 2

# ─── Power Words por língua ───────────────────────────────────
POWER_WORDS = {
    "en": ["secret", "exposed", "truth", "never", "always", "shocking", "banned",
           "warning", "mistake", "dead", "kill", "hidden", "nobody", "instantly",
           "proven", "leaked", "urgent", "stop", "before", "why", "how to",
           "what happens", "i tried", "tested"],
    "de": ["geheimnis", "enthüllt", "fehler", "warnung", "sofort", "verboten",
           "schockierend", "bewährt", "niemand"],
    "fr": ["secret", "révélé", "erreur", "urgent", "choquant", "prouvé",
           "interdit", "vérité"],
    "es": ["secreto", "revelado", "error", "urgente", "impactante", "probado",
           "nadie", "verdad", "cómo"],
}

SYSTEM_PROMPT = """You are a YouTube viral content analyst with 10+ years of data.
Your job: score a video topic/angle on its VIRAL POTENTIAL (0-100).

Scoring criteria (20 points each):
1. TITLE POWER (0-20): Power words, emotional triggers, controversy, curiosity words
2. CURIOSITY GAP (0-20): Does the topic create an irresistible open loop? "What is X?" = low. "Why 99% of people do X wrong" = high.
3. SEARCH VOLUME (0-20): Estimate based on niche and keyword trends. Mainstream + rising = 20. Niche + stable = 10. Obscure = 0.
4. COMPETITION GAP (0-20): Are there few recent, high-quality videos on this exact angle? Gap = 20. Saturated = 0.
5. TREND FACTOR (0-20): Is this topic trending NOW (2026)? Trending + evergreen = 20. Dying trend = 0.

If score < 70, suggest an OPTIMIZED version of the topic/angle that would score higher.
Respond ONLY with valid JSON, no markdown.
"""


def calculate_rule_based_score(topic: str, angle: str, language: str,
                                niche: str, competition_level: str) -> ViralScoreBreakdown:
    """
    Heurística rápida baseada em regras para scores preliminares.
    Complementado pelo LLM para precisão.
    """
    lang_prefix = language[:2].lower()
    words = POWER_WORDS.get(lang_prefix, POWER_WORDS["en"])
    topic_lower = (topic + " " + angle).lower()

    # 1. Title Power — conta power words
    pw_count = sum(1 for w in words if w in topic_lower)
    title_power = min(20.0, pw_count * 5.0)

    # 2. Curiosity Gap — padrões de open loop
    gap_patterns = [
        r"why.*wrong", r"nobody.*tells", r"secret", r"truth about",
        r"what.*don.*t.*tell", r"stop doing", r"mistake", r"exposed"
    ]
    gap_hits = sum(1 for p in gap_patterns if re.search(p, topic_lower))
    curiosity_gap = min(20.0, gap_hits * 7.0)

    # 3. Search Volume — estima por niche CPM (proxy)
    niche_volume = {"ai_tools": 16, "personal_finance": 18,
                    "health_longevity": 14, "psychology_curiosity": 12}
    search_volume = float(niche_volume.get(niche, 10))

    # 4. Competition Gap
    comp_map = {"low": 18.0, "medium": 12.0, "high": 6.0}
    competition_gap = comp_map.get(competition_level, 10.0)

    # 5. Trend Factor — keywords de tendência 2026
    trend_words = ["ai", "2026", "2027", "gpt", "automation", "longevity",
                   "passive income", "inflation", "psychology", "hack"]
    trend_hits = sum(1 for w in trend_words if w in topic_lower)
    trend_factor = min(20.0, trend_hits * 4.0)

    return ViralScoreBreakdown(
        title_power=title_power,
        curiosity_gap=curiosity_gap,
        search_volume=search_volume,
        competition_gap=competition_gap,
        trend_factor=trend_factor,
    )


def run_viral_score_agent(
    job_id_str: str,
    niche_data_dict: dict,
    score_threshold: float = VIRAL_SCORE_THRESHOLD,
    retry_count: int = 0,
) -> ViralScoreResult:
    """
    Executa o Viral Score Agent.

    Args:
        job_id_str: UUID do job
        niche_data_dict: NicheData serializado
        score_threshold: Mínimo para passar (padrão: 70)
        retry_count: Número de tentativas já realizadas
    """
    from shared.models import NicheData

    niche_data = NicheData(**niche_data_dict)
    job_id = UUID(job_id_str)

    logger.info(f"🎯 [ViralScore] Scoring | job={job_id_str[:8]} | topic='{niche_data.topic[:50]}'")

    # ── Step 1: Score heurístico rápido ────────────────────────
    breakdown = calculate_rule_based_score(
        topic=niche_data.topic,
        angle=niche_data.angle,
        language=niche_data.language,
        niche=niche_data.niche if isinstance(niche_data.niche, str) else niche_data.niche.value,
        competition_level=niche_data.competition_level,
    )
    rule_score = breakdown.total

    # ── Step 2: Refinamento LLM ────────────────────────────────
    llm = get_llm_client()
    final_breakdown = breakdown
    improvements: list[str] = []
    optimized_topic: str | None = None
    optimized_angle: str | None = None

    try:
        user_prompt = f"""Score this YouTube video topic:

TOPIC: {niche_data.topic}
ANGLE: {niche_data.angle}
NICHE: {niche_data.niche}
LANGUAGE: {niche_data.language}
COMPETITION: {niche_data.competition_level}
PRELIMINARY RULE SCORE: {rule_score}/100

Provide your assessment. If score < {score_threshold}, provide an optimized version.

Return JSON:
{{
  "title_power": 0-20,
  "curiosity_gap": 0-20,
  "search_volume": 0-20,
  "competition_gap": 0-20,
  "trend_factor": 0-20,
  "total_score": 0-100,
  "improvements": ["improvement 1", "improvement 2"],
  "optimized_topic": "Better topic title if score < {score_threshold}, else null",
  "optimized_angle": "Better angle if score < {score_threshold}, else null"
}}"""

        response = llm.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=512,
            json_mode=True,
        )

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        final_breakdown = ViralScoreBreakdown(
            title_power=float(data.get("title_power", breakdown.title_power)),
            curiosity_gap=float(data.get("curiosity_gap", breakdown.curiosity_gap)),
            search_volume=float(data.get("search_volume", breakdown.search_volume)),
            competition_gap=float(data.get("competition_gap", breakdown.competition_gap)),
            trend_factor=float(data.get("trend_factor", breakdown.trend_factor)),
        )
        improvements = data.get("improvements", [])
        optimized_topic = data.get("optimized_topic")
        optimized_angle = data.get("optimized_angle")

    except Exception as e:
        logger.warning(f"[ViralScore] LLM refinement failed — using rule score: {e}")

    final_score = final_breakdown.total
    passed = final_score >= score_threshold

    logger.info(
        f"{'✅' if passed else '❌'} [ViralScore] score={final_score:.1f} | "
        f"{'PASSED' if passed else f'FAILED (threshold={score_threshold})'}"
    )

    result = ViralScoreResult(
        job_id=job_id,
        topic=niche_data.topic,
        angle=niche_data.angle,
        language=niche_data.language,
        niche=str(niche_data.niche),
        score=final_score,
        breakdown=final_breakdown,
        passed_gate=passed,
        rejection_reason=None if passed else f"Score {final_score:.1f} < threshold {score_threshold}",
        improvements=improvements,
        optimized_topic=optimized_topic,
        optimized_angle=optimized_angle,
        retry_count=retry_count,
    )

    # ── Step 3: Ação baseada no resultado ──────────────────────
    if passed:
        _forward_to_copywriter(niche_data_dict)
    elif retry_count < MAX_OPTIMIZATION_RETRIES and (optimized_topic or optimized_angle):
        logger.info(f"🔄 [ViralScore] Optimizing and retrying (attempt {retry_count + 1})...")
        _retry_with_optimization(job_id_str, niche_data_dict, optimized_topic,
                                  optimized_angle, retry_count + 1)
    else:
        _abort_job(job_id_str, niche_data, final_score)

    return result


def _forward_to_copywriter(niche_data_dict: dict) -> None:
    from agents.copywriter.agent import run_copywriter_agent
    from orchestrator.queue_manager import QUEUE_COPYWRITER, get_queue_manager
    qm = get_queue_manager()
    qm.enqueue(QUEUE_COPYWRITER, run_copywriter_agent, niche_data_dict)
    logger.info("→ [ViralScore] Forwarded to Copywriter")


def _retry_with_optimization(job_id_str: str, niche_data_dict: dict,
                               optimized_topic: str | None, optimized_angle: str | None,
                               retry_count: int) -> None:
    """Re-executa com tópico/ângulo otimizado."""
    from orchestrator.queue_manager import QUEUE_VIRAL_SCORE, get_queue_manager
    if optimized_topic:
        niche_data_dict["topic"] = optimized_topic
    if optimized_angle:
        niche_data_dict["angle"] = optimized_angle
    qm = get_queue_manager()
    qm.enqueue(QUEUE_VIRAL_SCORE, run_viral_score_agent,
               job_id_str, niche_data_dict, VIRAL_SCORE_THRESHOLD, retry_count)


def _abort_job(job_id_str: str, niche_data, score: float) -> None:
    """Loga job abortado no DB."""
    try:
        from database.db_manager import fail_pipeline_job
        fail_pipeline_job(
            job_id_str,
            f"Viral score {score:.1f} below threshold after {MAX_OPTIMIZATION_RETRIES} retries",
            retry_count=MAX_OPTIMIZATION_RETRIES,
        )
    except Exception as e:
        logger.warning(f"[ViralScore] DB log failed: {e}")
    logger.warning(f"🚫 [ViralScore] Job {job_id_str[:8]} ABORTED — score too low ({score:.1f})")
