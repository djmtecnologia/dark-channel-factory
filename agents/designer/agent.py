"""Designer Agent: conceitos Ollama + imagens Hugging Face + texto Pillow."""
from __future__ import annotations

import json
import os
from uuid import UUID

from agents.designer.thumbnail_generator import generate_thumbnail_variants
from orchestrator.queue_manager import QUEUE_UPLOAD, get_queue_manager
from shared.logger import get_logger
from shared.models import ScriptData, ThumbnailData, ThumbnailVariant
from shared.ollama_client import get_ollama_client

logger = get_logger(__name__)


def run_designer_agent(script_data_dict: dict) -> ThumbnailData:
    script = ScriptData(**script_data_dict)
    concepts = None
    try:
        concepts = get_ollama_client().chat_json(
            "You design high-CTR YouTube thumbnails. Return JSON only.",
            f"""Create 3 distinct background concepts for this title: {script.title}
Language: {script.language}
Brief: {script.thumbnail_brief}
Return {{"variants":[{{"prompt":"English image prompt, no text"}}, ...]}}.
Reserve the upper area for title overlay and forbid letters and watermarks.""",
            temperature=0.65,
            num_predict=1000,
        )
    except Exception as exc:
        logger.warning("[Designer] Conceitos Ollama falharam; usando briefs padrão: %s", exc)

    variants = generate_thumbnail_variants(
        job_id=script.job_id,
        title=script.title,
        thumbnail_brief=script.thumbnail_brief,
        dominant_color=script.thumbnail_dominant_color,
        primary_keyword=script.niche_data.primary_keyword,
        variant_prompts=concepts,
    )
    result = ThumbnailData(
        job_id=script.job_id,
        variants=[ThumbnailVariant(**item) for item in variants],
        active_variant="A",
    )
    _store_thumbnail_data(result)
    _try_trigger_upload(script.job_id)
    return result


def _redis():
    import redis
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _store_thumbnail_data(data: ThumbnailData) -> None:
    _redis().setex(
        f"thumbnail_data:{data.job_id}",
        86400,
        json.dumps(data.model_dump(mode="json"), default=str),
    )


def _try_trigger_upload(job_id: UUID) -> None:
    if _redis().get(f"video_data:{job_id}"):
        from agents.manager_analyst.agent import run_upload_agent
        get_queue_manager().enqueue(QUEUE_UPLOAD, run_upload_agent, str(job_id), job_timeout=1800)
