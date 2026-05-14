"""
Feedback Loop — Manager & Analyst Agent
Executa ações baseadas nas métricas de analytics.
"""
from __future__ import annotations

from shared.logger import get_logger

logger = get_logger(__name__)


def execute_feedback_actions(
    video_record: dict,
    actions: list[str],
) -> dict:
    """
    Executa ações de retroalimentação baseadas nas métricas.

    Actions possíveis:
    - swap_thumbnail:X → troca thumbnail para variante X
    - trigger_deep_dive:TOPIC → dispara nosso agente Scout para deep dive
    - scale_production → aumenta frequência de publicação
    """
    updated_record = dict(video_record)

    for action in actions:
        if action.startswith("swap_thumbnail:"):
            new_variant = action.split(":")[1]
            _execute_thumbnail_swap(video_record, new_variant, updated_record)

        elif action.startswith("trigger_deep_dive:"):
            topic = ":".join(action.split(":")[1:])
            _trigger_deep_dive(video_record, topic, updated_record)

        elif action == "scale_production":
            logger.info(
                f"🔥 [FeedbackLoop] Viral video detected! "
                f"Consider increasing publish frequency for channel {video_record.get('channel_id')}"
            )

    return updated_record


def _execute_thumbnail_swap(video_record: dict, new_variant: str, updated_record: dict) -> None:
    """Troca thumbnail no YouTube e atualiza registro local."""
    from agents.manager_analyst.youtube_uploader import update_thumbnail

    video_id = video_record.get("youtube_video_id")
    thumbnail_paths = video_record.get("thumbnail_paths", [])

    # Encontra path da nova variante
    variant_index = ord(new_variant) - ord("A")
    if variant_index < len(thumbnail_paths):
        new_thumb_path = thumbnail_paths[variant_index]
        success = update_thumbnail(video_id, new_thumb_path)

        if success:
            updated_record["active_thumbnail_variant"] = new_variant
            updated_record["thumbnail_swapped_count"] = video_record.get("thumbnail_swapped_count", 0) + 1
            logger.info(f"✅ [FeedbackLoop] Thumbnail swapped to {new_variant} for {video_id}")
        else:
            logger.error(f"[FeedbackLoop] Thumbnail swap failed for {video_id}")
    else:
        logger.warning(f"[FeedbackLoop] No thumbnail path for variant {new_variant}")


def _trigger_deep_dive(video_record: dict, parent_topic: str, updated_record: dict) -> None:
    """Dispara pipeline de deep dive para tópico com alta retenção."""
    from agents.scout_miner.agent import run_scout_agent
    from orchestrator.queue_manager import QUEUE_SCOUT, get_queue_manager
    from uuid import uuid4

    logger.info(f"🚀 [FeedbackLoop] Triggering deep dive for: '{parent_topic}'")

    try:
        qm = get_queue_manager()
        new_job_id = str(uuid4())
        channel_id = video_record.get("channel_id", "ai_tools_us")

        qm.enqueue(
            QUEUE_SCOUT,
            run_scout_agent,
            new_job_id,
            channel_id,
            True,   # is_deep_dive=True
            parent_topic,
            video_record.get("youtube_video_id"),
            video_record.get("analytics", {}).get("avg_view_duration_percent", 50.0),
            job_timeout=900,
        )

        updated_record["deep_dive_triggered"] = True
        logger.info(f"✅ [FeedbackLoop] Deep dive queued | new_job={new_job_id}")

    except Exception as e:
        logger.error(f"[FeedbackLoop] Deep dive trigger failed: {e}")
