"""
Database Manager — Dark Channel Factory v2
Abstração SQLite (dev) / PostgreSQL via DATABASE_URL (produção/Neon).
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from dotenv import load_dotenv

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", "output/logs/factory.db"))


def _init_sqlite(db_path: Path) -> sqlite3.Connection:
    """Inicializa banco SQLite com schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    schema_path = Path(__file__).parent / "schema.sql"
    if schema_path.exists():
        with open(schema_path, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager para conexão com o banco."""
    if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
        # PostgreSQL via psycopg2 (Neon/Supabase)
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(DATABASE_URL)
            try:
                yield conn
            finally:
                conn.close()
        except ImportError:
            logger.warning("psycopg2 not installed — falling back to SQLite")
            conn = _init_sqlite(SQLITE_PATH)
            try:
                yield conn
            finally:
                conn.close()
    else:
        conn = _init_sqlite(SQLITE_PATH)
        try:
            yield conn
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
# Videos
# ─────────────────────────────────────────────────────────────

def upsert_video(record: dict) -> None:
    """Insere ou atualiza registro de vídeo."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO videos (
                id, youtube_video_id, channel_id, title, niche, language,
                country_code, topic, angle, primary_keyword, viral_score,
                validation_passed, created_at
            ) VALUES (
                :id, :youtube_video_id, :channel_id, :title, :niche, :language,
                :country_code, :topic, :angle, :primary_keyword, :viral_score,
                :validation_passed, :created_at
            )
            ON CONFLICT(id) DO UPDATE SET
                youtube_video_id=excluded.youtube_video_id,
                ctr_percent=excluded.ctr_percent,
                retention_percent=excluded.retention_percent,
                views=excluded.views,
                likes=excluded.likes,
                impressions=excluded.impressions,
                estimated_revenue_usd=excluded.estimated_revenue_usd,
                active_thumbnail=excluded.active_thumbnail,
                last_analytics_at=excluded.last_analytics_at
        """, {
            "id": record.get("id", ""),
            "youtube_video_id": record.get("youtube_video_id", ""),
            "channel_id": record.get("channel_id", ""),
            "title": record.get("title", ""),
            "niche": record.get("niche", ""),
            "language": record.get("language", "en-US"),
            "country_code": record.get("country_code", "US"),
            "topic": record.get("topic", ""),
            "angle": record.get("angle", ""),
            "primary_keyword": record.get("primary_keyword", ""),
            "viral_score": record.get("viral_score", 0),
            "validation_passed": int(record.get("validation_passed", True)),
            "created_at": record.get("created_at", _now()),
        })
        conn.commit()
    logger.debug(f"DB: video upserted {record.get('id', '')[:8]}")


def update_video_analytics(youtube_video_id: str, analytics: dict) -> None:
    """Atualiza métricas de analytics de um vídeo."""
    with get_db() as conn:
        conn.execute("""
            UPDATE videos SET
                ctr_percent=:ctr,
                retention_percent=:retention,
                views=:views,
                likes=:likes,
                comments=:comments,
                impressions=:impressions,
                estimated_revenue_usd=:revenue,
                last_analytics_at=:checked_at
            WHERE youtube_video_id=:video_id
        """, {
            "video_id": youtube_video_id,
            "ctr": analytics.get("ctr_percent", 0),
            "retention": analytics.get("avg_view_duration_percent", 0),
            "views": analytics.get("views", 0),
            "likes": analytics.get("likes", 0),
            "comments": analytics.get("comments", 0),
            "impressions": analytics.get("impressions", 0),
            "revenue": analytics.get("estimated_revenue_usd", 0),
            "checked_at": _now(),
        })
        conn.commit()


def get_top_performing_videos(niche: str = "", language: str = "", limit: int = 10) -> list[dict]:
    """Retorna top vídeos por CTR."""
    with get_db() as conn:
        query = "SELECT * FROM videos WHERE ctr_percent > 0"
        params: list[Any] = []
        if niche:
            query += " AND niche=?"
            params.append(niche)
        if language:
            query += " AND language=?"
            params.append(language)
        query += " ORDER BY ctr_percent DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────
# Topics History
# ─────────────────────────────────────────────────────────────

def save_topic(topic: str, niche: str, language: str, viral_score: float,
               angle: str = "", video_id: str = "") -> None:
    """Salva tópico no histórico."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO topics_history (topic, niche, language, angle, viral_score, used_at, video_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (topic, niche, language, angle, viral_score, _now(), video_id))
        conn.commit()


def topic_exists(topic: str, niche: str, language: str, similarity_threshold: int = 80) -> bool:
    """Verifica se tópico similar já existiu (comparação simples por palavras-chave)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT topic FROM topics_history WHERE niche=? AND language=? ORDER BY used_at DESC LIMIT 50",
            (niche, language)
        ).fetchall()

    topic_words = set(topic.lower().split())
    for row in rows:
        existing_words = set(row["topic"].lower().split())
        if len(topic_words) > 0:
            overlap = len(topic_words & existing_words) / len(topic_words) * 100
            if overlap >= similarity_threshold:
                return True
    return False


# ─────────────────────────────────────────────────────────────
# Winning Patterns
# ─────────────────────────────────────────────────────────────

def upsert_winning_pattern(pattern_type: str, pattern_value: str, niche: str,
                            language: str, ctr: float, retention: float) -> None:
    """Insere ou atualiza padrão vencedor."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO winning_patterns (pattern_type, pattern_value, niche, language,
                                          avg_ctr, avg_retention, sample_size, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(pattern_type, pattern_value, niche, language) DO UPDATE SET
                avg_ctr = (avg_ctr * sample_size + excluded.avg_ctr) / (sample_size + 1),
                avg_retention = (avg_retention * sample_size + excluded.avg_retention) / (sample_size + 1),
                sample_size = sample_size + 1,
                updated_at = excluded.updated_at
        """, (pattern_type, pattern_value, niche, language, ctr, retention, _now()))
        conn.commit()


def get_winning_patterns(niche: str = "", language: str = "", limit: int = 20) -> list[dict]:
    """Retorna padrões vencedores ordenados por CTR médio."""
    with get_db() as conn:
        query = "SELECT * FROM winning_patterns WHERE sample_size >= 2"
        params: list[Any] = []
        if niche:
            query += " AND niche=?"
            params.append(niche)
        if language:
            query += " AND language=?"
            params.append(language)
        query += " ORDER BY avg_ctr DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────
# Pipeline Jobs
# ─────────────────────────────────────────────────────────────

def save_pipeline_job(job_id: str, channel_id: str, status: str,
                       niche: str = "", language: str = "", topic: str = "") -> None:
    """Registra ou atualiza estado de um job na pipeline."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO pipeline_jobs (id, channel_id, status, niche, language, topic, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                topic=excluded.topic
        """, (job_id, channel_id, status, niche, language, topic, _now()))
        conn.commit()


def fail_pipeline_job(job_id: str, error_message: str, retry_count: int = 0) -> None:
    """Marca job como falho com mensagem de erro."""
    with get_db() as conn:
        conn.execute("""
            UPDATE pipeline_jobs SET status='failed', error_message=?, retry_count=?
            WHERE id=?
        """, (error_message[:1000], retry_count, job_id))
        conn.commit()


def get_failed_jobs(limit: int = 50) -> list[dict]:
    """Retorna jobs que falharam para retry."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_jobs WHERE status='failed' AND retry_count < 3 ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
