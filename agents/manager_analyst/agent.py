"""
Manager & Analyst Agent — Dark Channel Factory
Agent 5: Upload do vídeo, comentário fixado, agendamento e monitoramento.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from agents.manager_analyst.analytics_monitor import (
    analyze_and_act,
    fetch_video_analytics,
    load_published_videos,
    save_published_video,
)
from agents.manager_analyst.feedback_loop import execute_feedback_actions
from agents.manager_analyst.youtube_uploader import (
    get_scheduled_publish_time,
    post_pinned_comment,
    upload_video,
)
from orchestrator.config import get_channel, get_default_channel
from shared.logger import get_logger
from shared.models import PublishedVideo, ScriptData, ThumbnailData, VideoData, VideoStatus

load_dotenv()
logger = get_logger(__name__)


def run_upload_agent(job_id_str: str) -> PublishedVideo | None:
    """
    Executa upload consolidando VideoData + ThumbnailData do Redis.

    Args:
        job_id_str: String UUID do job
    """
    logger.info(f"📤 [Manager] Starting upload | job={job_id_str}")

    r = _get_redis()
    job_id = UUID(job_id_str)

    # ── Recuperar dados do Redis ───────────────────────────────
    video_raw = r.get(f"video_data:{job_id}")
    thumbnail_raw = r.get(f"thumbnail_data:{job_id}")

    if not video_raw:
        logger.error(f"[Manager] VideoData not found in Redis for job {job_id}")
        return None

    if not thumbnail_raw:
        logger.warning(f"[Manager] ThumbnailData not found — uploading without thumbnail")

    video_data = VideoData(**json.loads(video_raw))
    thumbnail_data = ThumbnailData(**json.loads(thumbnail_raw)) if thumbnail_raw else None

    script_data = video_data.script_data
    channel = get_channel(script_data.niche_data.channel_id) or get_default_channel()

    # ── Verificar arquivo de vídeo ─────────────────────────────
    if not Path(video_data.video_path).exists():
        logger.error(f"[Manager] Video file missing: {video_data.video_path}")
        return None

    # ── Calcular horário de publicação ─────────────────────────
    scheduled_at = get_scheduled_publish_time(
        publish_days=channel.publish_days,
        publish_time_utc=channel.publish_time_utc,
    )
    logger.info(f"📅 [Manager] Scheduled for: {scheduled_at.isoformat()}")

    # ── Upload para YouTube ────────────────────────────────────
    active_thumb_path = thumbnail_data.get_active_path() if thumbnail_data else None

    youtube_video_id = upload_video(
        video_path=video_data.video_path,
        title=script_data.title,
        description=script_data.description,
        tags=script_data.tags,
        category_id=script_data.category_id,
        thumbnail_path=active_thumb_path,
        scheduled_at=scheduled_at,
    )

    if not youtube_video_id:
        logger.error("[Manager] Upload failed — no video ID returned")
        return None

    # ── Postar comentário fixado com afiliados ────────────────
    comment_id = None
    if script_data.pinned_comment:
        comment_id = post_pinned_comment(youtube_video_id, script_data.pinned_comment)

    # ── Construir registro PublishedVideo ─────────────────────
    thumbnail_paths = [v.path for v in thumbnail_data.variants] if thumbnail_data else []

    published = PublishedVideo(
        job_id=job_id,
        youtube_video_id=youtube_video_id,
        scheduled_publish_at=scheduled_at,
        title=script_data.title,
        status=VideoStatus.SCHEDULED,
        video_path=video_data.video_path,
        thumbnail_paths=thumbnail_paths,
        active_thumbnail_variant="A",
        affiliate_links=script_data.affiliate_links,
        pinned_comment_id=comment_id,
        niche_data=script_data.niche_data,
    )

    # ── Salvar no banco local ──────────────────────────────────
    record = {
        **published.model_dump(mode="json"),
        "channel_id": channel.id,
        "topic": script_data.niche_data.topic,
        "analytics": {},
    }
    save_published_video(record)

    # ── Limpar Redis ───────────────────────────────────────────
    r.delete(f"video_data:{job_id}")
    r.delete(f"thumbnail_data:{job_id}")

    logger.info(
        f"✅ [Manager] Upload complete! "
        f"Video ID: {youtube_video_id} | "
        f"Scheduled: {scheduled_at.strftime('%a %d/%m %H:%M UTC')}"
    )

    return published


def run_analytics_monitor() -> None:
    """
    Executa verificação de analytics em todos os vídeos publicados.
    Chamado pelo orquestrador a cada 48 horas.
    """
    logger.info("📊 [Manager] Running analytics monitor...")
    records = load_published_videos()

    if not records:
        logger.info("[Manager] No published videos to monitor")
        return

    for record in records:
        video_id = record.get("youtube_video_id")
        if not video_id or record.get("deep_dive_triggered"):
            continue

        logger.info(f"📊 [Manager] Checking analytics for: {video_id}")

        # Busca métricas
        analytics = fetch_video_analytics(video_id)
        if not analytics:
            logger.warning(f"[Manager] No analytics data for {video_id} — skipping")
            continue

        # Atualiza registro com novas métricas
        record["analytics"] = analytics
        record["last_analytics_check"] = datetime.now(timezone.utc).isoformat()

        # Analisa e determina ações
        actions = analyze_and_act(record, analytics)

        # Executa ações de feedback loop
        if actions:
            updated_record = execute_feedback_actions(record, actions)
            save_published_video(updated_record)
        else:
            save_published_video(record)

    logger.info(f"✅ [Manager] Analytics check complete for {len(records)} videos")


def _get_redis():
    """Retorna conexão Redis."""
    import redis

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(redis_url)
