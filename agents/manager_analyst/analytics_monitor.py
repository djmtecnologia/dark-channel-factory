"""
Analytics Monitor — Manager & Analyst Agent
Monitora métricas de CTR e retenção dos vídeos publicados.
Dispara troca de thumbnail e deep dive quando necessário.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "credentials/youtube_token.json")

CTR_THRESHOLD = float(os.getenv("CTR_THRESHOLD_PERCENT", "4.0"))
RETENTION_THRESHOLD = float(os.getenv("RETENTION_THRESHOLD_PERCENT", "45.0"))

# Arquivo de estado local (rastreia vídeos publicados)
PUBLISHED_VIDEOS_DB = Path("output/logs/published_videos.json")


def load_published_videos() -> list[dict]:
    """Carrega lista de vídeos publicados do arquivo de estado."""
    if not PUBLISHED_VIDEOS_DB.exists():
        return []
    with open(PUBLISHED_VIDEOS_DB, "r") as f:
        return json.load(f)


def save_published_video(video_record: dict) -> None:
    """Adiciona/atualiza registro de vídeo publicado."""
    PUBLISHED_VIDEOS_DB.parent.mkdir(parents=True, exist_ok=True)
    records = load_published_videos()

    # Atualiza se já existe
    for i, r in enumerate(records):
        if r.get("youtube_video_id") == video_record.get("youtube_video_id"):
            records[i] = {**r, **video_record}
            break
    else:
        records.append(video_record)

    with open(PUBLISHED_VIDEOS_DB, "w") as f:
        json.dump(records, f, indent=2, default=str)


def fetch_video_analytics(video_id: str) -> dict:
    """
    Busca métricas de analytics de um vídeo.
    Retorna dict com CTR, retenção, views, etc.
    """
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        token_path = Path(YOUTUBE_TOKEN_FILE)
        if not token_path.exists():
            logger.warning("YouTube token not found — skipping analytics")
            return {}

        creds = Credentials.from_authorized_user_file(str(token_path))
        youtube = build("youtube", "v3", credentials=creds)

        # Busca estatísticas básicas
        response = youtube.videos().list(
            part="statistics,contentDetails",
            id=video_id,
        ).execute()

        items = response.get("items", [])
        if not items:
            return {}

        stats = items[0].get("statistics", {})
        content = items[0].get("contentDetails", {})

        # Busca analytics avançados via YouTube Analytics API (requer escopo adicional)
        analytics = _fetch_advanced_analytics(video_id, creds)

        return {
            "video_id": video_id,
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "ctr_percent": analytics.get("ctr", 0.0),
            "avg_view_duration_percent": analytics.get("avg_view_percentage", 0.0),
            "impressions": analytics.get("impressions", 0),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Analytics fetch failed for {video_id}: {e}")
        return {}


def _fetch_advanced_analytics(video_id: str, creds) -> dict:
    """
    Busca CTR e retenção via YouTube Analytics API.
    """
    try:
        from googleapiclient.discovery import build

        analytics_service = build("youtubeAnalytics", "v2", credentials=creds)

        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = "2024-01-01"

        response = analytics_service.reports().query(
            ids=f"channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="impressions,impressionClickThroughRate,averageViewPercentage,views",
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()

        rows = response.get("rows", [])
        if rows:
            row = rows[0]
            return {
                "impressions": int(row[1]),
                "ctr": float(row[2]) * 100,  # Converte de decimal para porcentagem
                "avg_view_percentage": float(row[3]),
                "views": int(row[4]),
            }
        return {}

    except Exception as e:
        logger.warning(f"Advanced analytics unavailable: {e}")
        return {}


def analyze_and_act(video_record: dict, analytics: dict) -> list[str]:
    """
    Analisa métricas e retorna lista de ações a tomar.

    Returns:
        Lista de ações: ["swap_thumbnail", "trigger_deep_dive", etc]
    """
    actions: list[str] = []

    ctr = analytics.get("ctr_percent", 0.0)
    retention = analytics.get("avg_view_duration_percent", 0.0)
    thumbnail_variant = video_record.get("active_thumbnail_variant", "A")
    swap_count = video_record.get("thumbnail_swapped_count", 0)

    logger.info(
        f"📊 Analytics [{video_record.get('youtube_video_id')}] "
        f"CTR={ctr:.1f}% | Retention={retention:.1f}% | Thumbnail={thumbnail_variant}"
    )

    # Regra 1: CTR baixo → troca thumbnail
    if ctr < CTR_THRESHOLD and swap_count < 2:
        next_variant = {"A": "B", "B": "C"}.get(thumbnail_variant)
        if next_variant:
            actions.append(f"swap_thumbnail:{next_variant}")
            logger.info(f"📌 Action: Swap thumbnail {thumbnail_variant} → {next_variant} (CTR={ctr:.1f}%)")

    # Regra 2: Retenção alta → dispara deep dive
    if retention > RETENTION_THRESHOLD and not video_record.get("deep_dive_triggered"):
        actions.append(f"trigger_deep_dive:{video_record.get('topic', 'unknown')}")
        logger.info(f"🚀 Action: Trigger deep dive (retention={retention:.1f}%)")

    # Regra 3: Vídeo viral (>100k views) → escalar produção
    if analytics.get("views", 0) > 100_000:
        actions.append("scale_production")
        logger.info("🔥 Action: Viral video — scale production mode!")

    if not actions:
        logger.info(f"✓ No actions needed | CTR={ctr:.1f}% | Retention={retention:.1f}%")

    return actions
