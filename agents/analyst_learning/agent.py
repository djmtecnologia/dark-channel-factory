"""
Analyst & Learning Agent — Dark Channel Factory v2
Módulo 9: Armazena performance histórica e realimenta o Scout com padrões vencedores.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.models_v2 import LearningInsight, WinningPattern

load_dotenv()
logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a data scientist specialized in YouTube channel growth analytics.
Your job: analyze performance data from our video library and identify WINNING PATTERNS
that should be replicated across new languages and niches.

Focus on:
1. Title structures that generate high CTR (>6%)
2. Thumbnail styles that perform above average
3. Hook formulas that achieve >50% retention
4. Niches showing growth opportunity
5. Topics that work in one language and should be replicated in others

Return actionable insights that the Scout Agent can immediately use.
Respond with valid JSON only.
"""


def run_analytics_monitor() -> None:
    """
    Chamado pelo orquestrador a cada 48h.
    Busca analytics de todos os vídeos publicados e atualiza o DB.
    """
    from agents.manager_analyst.analytics_monitor import (
        load_published_videos, fetch_video_analytics, analyze_and_act
    )
    from agents.manager_analyst.feedback_loop import execute_feedback_actions
    from agents.manager_analyst.analytics_monitor import save_published_video
    from database.db_manager import update_video_analytics, upsert_winning_pattern

    logger.info("📊 [Analyst] Running analytics monitor...")
    records = load_published_videos()

    if not records:
        logger.info("[Analyst] No published videos to monitor")
        return

    processed = 0
    for record in records:
        video_id = record.get("youtube_video_id")
        if not video_id:
            continue

        analytics = fetch_video_analytics(video_id)
        if not analytics:
            continue

        # Atualiza DB local (JSON)
        record["analytics"] = analytics
        record["last_analytics_at"] = datetime.now(timezone.utc).isoformat()
        save_published_video(record)

        # Atualiza DB de aprendizado
        try:
            update_video_analytics(video_id, analytics)
        except Exception as e:
            logger.warning(f"[Analyst] DB update failed for {video_id}: {e}")

        # Aprende padrões se vídeo tem bom CTR
        ctr = analytics.get("ctr_percent", 0)
        retention = analytics.get("avg_view_duration_percent", 0)

        if ctr >= 4.0:
            _extract_and_save_patterns(record, ctr, retention)

        # Feedback loop (troca thumbnail, deep dive, etc)
        actions = analyze_and_act(record, analytics)
        if actions:
            execute_feedback_actions(record, actions)

        processed += 1

    logger.info(f"✅ [Analyst] Analytics updated for {processed}/{len(records)} videos")

    # Gera insight de aprendizado se tem dados suficientes
    if processed >= 3:
        try:
            insight = generate_learning_insights()
            _broadcast_insight_to_scout(insight)
        except Exception as e:
            logger.warning(f"[Analyst] Insight generation failed: {e}")


def generate_learning_insights(niche: str = "", language: str = "") -> LearningInsight:
    """
    Gera insights de aprendizado baseado no histórico do DB.
    Usa LLM para sintetizar padrões complexos.
    """
    from database.db_manager import (
        get_top_performing_videos, get_winning_patterns, get_failed_jobs
    )

    logger.info("🧠 [Analyst] Generating learning insights...")

    top_videos = get_top_performing_videos(niche=niche, language=language, limit=10)
    patterns = get_winning_patterns(niche=niche, language=language, limit=20)
    failed = get_failed_jobs(limit=20)

    if not top_videos and not patterns:
        return LearningInsight()

    llm = get_llm_client()

    user_prompt = f"""Analyze our YouTube channel performance data:

TOP PERFORMING VIDEOS (by CTR):
{_format_list(top_videos[:5])}

WINNING PATTERNS LEARNED:
{_format_list(patterns[:10])}

FAILED JOBS (for avoidance):
{_format_list(failed[:5])}

Generate strategic insights. Return JSON:
{{
  "top_niches": ["niche1", "niche2"],
  "top_title_patterns": ["pattern like 'Why X is wrong'", "pattern 2"],
  "top_thumbnail_styles": ["face + shock text", "data visualization"],
  "top_hooks": ["Most people don't know...", "hook 2"],
  "replicate_topics": [
    {{"topic": "original topic", "original_language": "en-US", "target_languages": ["de-DE", "fr-FR"], "estimated_ctr": 6.5}}
  ],
  "top_affiliate_products": ["product1", "product2"],
  "avg_affiliate_ctr": 2.1,
  "recommended_new_markets": ["de-DE", "fr-FR"]
}}"""

    try:
        response = llm.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=1024,
            json_mode=True,
        )
        import json
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        insight = LearningInsight(
            top_niches=data.get("top_niches", []),
            top_title_patterns=data.get("top_title_patterns", []),
            top_thumbnail_styles=data.get("top_thumbnail_styles", []),
            top_hooks=data.get("top_hooks", []),
            replicate_topics=data.get("replicate_topics", []),
            top_affiliate_products=data.get("top_affiliate_products", []),
            avg_affiliate_ctr=float(data.get("avg_affiliate_ctr", 0)),
            recommended_new_markets=data.get("recommended_new_markets", []),
        )
        logger.info(f"✅ [Analyst] Insight generated | top_niches={insight.top_niches}")
        return insight

    except Exception as e:
        logger.warning(f"[Analyst] LLM insight failed: {e}")
        return LearningInsight()


def _extract_and_save_patterns(record: dict, ctr: float, retention: float) -> None:
    """Extrai padrões de um vídeo com bom desempenho e salva no DB."""
    from database.db_manager import upsert_winning_pattern

    title = record.get("title", "")
    niche = record.get("niche", "ai_tools")
    language = record.get("language", "en-US")

    # Identifica padrão de título
    title_pattern = _classify_title_pattern(title)
    if title_pattern:
        upsert_winning_pattern("title_structure", title_pattern, niche, language, ctr, retention)

    # Thumbnail vencedor
    active_thumb = record.get("active_thumbnail_variant", "A")
    thumb_swaps = record.get("thumbnail_swapped_count", 0)
    if ctr >= 6.0 and thumb_swaps == 0:
        upsert_winning_pattern("thumbnail_style", f"variant_{active_thumb}", niche, language, ctr, retention)

    logger.debug(f"[Analyst] Patterns saved for {record.get('youtube_video_id', 'unknown')}")


def _classify_title_pattern(title: str) -> str | None:
    """Classifica o padrão de título de um vídeo vencedor."""
    import re

    title_lower = title.lower()

    patterns = [
        (r"\d+\s+(ways|tools|reasons|tips|mistakes)", "list_format"),
        (r"why.*wrong|why.*mistake", "contrarian_why"),
        (r"how to.*in \d+", "how_to_steps"),
        (r"secret|nobody|never tell", "secret_reveal"),
        (r"i tried|i tested|i used", "personal_experiment"),
        (r"vs|versus|compare", "comparison"),
        (r"\$[\d,]+|made.*\$|earn", "money_result"),
        (r"truth about|honest review|real review", "honest_review"),
    ]

    for regex, pattern_name in patterns:
        if re.search(regex, title_lower):
            return pattern_name

    return None


def _broadcast_insight_to_scout(insight: LearningInsight) -> None:
    """Salva insight para o Scout Agent usar na próxima mineração."""
    output_path = Path("output/logs/latest_insight.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import json
    with open(output_path, "w") as f:
        json.dump(insight.model_dump(mode="json"), f, indent=2, default=str)

    logger.info(f"💡 [Analyst] Insight saved → Scout will use on next run: {output_path}")


def _format_list(items: list) -> str:
    """Formata lista para o prompt do LLM."""
    if not items:
        return "No data available"
    return "\n".join(f"- {str(item)[:200]}" for item in items[:5])
