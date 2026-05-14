"""
Designer Agent — Dark Channel Factory
Agent 4: Gera 3 variantes de thumbnail (A/B/C) para teste de CTR.
Publica ThumbnailData que o Manager Agent consolida com VideoData.
"""
from __future__ import annotations

import json
import os
from uuid import UUID

from dotenv import load_dotenv

from agents.designer.prompts import THUMBNAIL_SYSTEM_PROMPT, THUMBNAIL_USER_PROMPT_TEMPLATE
from agents.designer.thumbnail_generator import generate_thumbnail_variants
from orchestrator.queue_manager import QUEUE_UPLOAD, get_queue_manager
from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.models import ScriptData, ThumbnailData, ThumbnailVariant

load_dotenv()
logger = get_logger(__name__)


def run_designer_agent(script_data_dict: dict) -> ThumbnailData:
    """
    Executa o Designer Agent.
    Gera 3 variantes de thumbnail e tenta consolidar com VideoData no Manager.

    Args:
        script_data_dict: ScriptData serializado como dict
    """
    script_data = ScriptData(**script_data_dict)
    job_id = script_data.job_id

    logger.info(f"🎨 [Designer] Starting | job={job_id}")

    llm = get_llm_client()

    # ── Step 1: Gerar prompts via LLM ─────────────────────────
    user_prompt = THUMBNAIL_USER_PROMPT_TEMPLATE.format(
        title=script_data.title,
        dominant_color=script_data.thumbnail_dominant_color,
        thumbnail_brief=script_data.thumbnail_brief,
        primary_keyword=script_data.niche_data.primary_keyword,
    )

    variant_prompts = None
    try:
        logger.info("🤖 [Designer] Generating thumbnail prompts via LLM...")
        llm_response = llm.complete(
            system_prompt=THUMBNAIL_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.9,
            max_tokens=2048,
            json_mode=True,
        )
        raw = llm_response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        variant_prompts = json.loads(raw)
        logger.info("✓ [Designer] Thumbnail prompts generated")
    except Exception as e:
        logger.warning(f"[Designer] LLM prompt generation failed: {e} — using defaults")

    # ── Step 2: Gerar thumbnails ───────────────────────────────
    logger.info("🖼️ [Designer] Generating thumbnail images...")
    variants_data = generate_thumbnail_variants(
        job_id=job_id,
        title=script_data.title,
        thumbnail_brief=script_data.thumbnail_brief,
        dominant_color=script_data.thumbnail_dominant_color,
        primary_keyword=script_data.niche_data.primary_keyword,
        variant_prompts=variant_prompts,
    )

    # ── Step 3: Construir ThumbnailData ───────────────────────
    thumbnail_data = ThumbnailData(
        job_id=job_id,
        variants=[ThumbnailVariant(**v) for v in variants_data],
        active_variant="A",
    )

    logger.info(
        f"✅ [Designer] ThumbnailData ready | variants={len(thumbnail_data.variants)} | "
        f"active={thumbnail_data.active_variant}"
    )

    # ── Step 4: Armazenar no Redis e tentar consolidar ─────────
    _store_thumbnail_data(thumbnail_data)
    _try_trigger_upload(job_id, script_data)

    return thumbnail_data


def _store_thumbnail_data(thumbnail_data: ThumbnailData) -> None:
    """Armazena ThumbnailData no Redis."""
    import redis
    import json

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)

    key = f"thumbnail_data:{thumbnail_data.job_id}"
    r.setex(
        key,
        86400,
        json.dumps(thumbnail_data.model_dump(mode="json"), default=str)
    )
    logger.debug(f"ThumbnailData stored in Redis key: {key}")


def _try_trigger_upload(job_id: UUID, script_data: ScriptData) -> None:
    """
    Tenta iniciar upload assim que Video E Thumbnail estão prontos.
    Verifica Redis para ver se VideoData já existe.
    """
    import redis
    import json

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)

    video_key = f"video_data:{job_id}"
    video_raw = r.get(video_key)

    if video_raw:
        logger.info(f"✅ [Designer] VideoData found in Redis — triggering upload!")
        from agents.manager_analyst.agent import run_upload_agent
        qm = get_queue_manager()
        qm.enqueue(
            QUEUE_UPLOAD,
            run_upload_agent,
            str(job_id),
            job_timeout=1800,
        )
    else:
        logger.info(f"⏳ [Designer] VideoData not yet ready — upload will be triggered by VideoEditor")
