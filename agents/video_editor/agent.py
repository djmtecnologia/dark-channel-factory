"""
Video Editor Agent — Dark Channel Factory
Agent 3: TTS + B-roll + FFmpeg → .mp4 final. Enfileira para Manager.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from agents.video_editor.media_fetcher import fetch_all_broll
from agents.video_editor.subtitle_generator import (
    generate_hormozi_subtitles,
    get_audio_duration,
)
from agents.video_editor.tts_engine import generate_full_narration
from agents.video_editor.video_assembler import assemble_video
from orchestrator.config import get_channel, get_default_channel
from orchestrator.queue_manager import QUEUE_UPLOAD, get_queue_manager
from shared.logger import get_logger
from shared.models import ScriptData, VideoData
from shared.storage import job_audio_path, job_video_path, cleanup_temp_files

load_dotenv()
logger = get_logger(__name__)


def run_video_editor_agent(script_data_dict: dict) -> VideoData:
    """
    Executa o Video Editor Agent.

    Pipeline:
    1. Gera narração TTS (edge-tts — gratuito)
    2. Baixa B-roll (Pexels/Pixabay — gratuito)
    3. Gera legendas Hormozi
    4. Monta vídeo final via FFmpeg
    5. Enfileira VideoData para Manager Agent

    Args:
        script_data_dict: ScriptData serializado como dict
    """
    script_data = ScriptData(**script_data_dict)
    job_id = script_data.job_id
    logger.info(f"🎬 [VideoEditor] Starting | job={job_id} | scenes={len(script_data.scenes)}")

    channel = get_channel(script_data.niche_data.channel_id) or get_default_channel()
    scenes_dicts = [s.model_dump(mode="json") for s in script_data.scenes]

    # ── Step 1: Gerar Narração TTS ─────────────────────────────
    logger.info("🎤 [VideoEditor] Generating TTS narration...")
    audio_paths = generate_full_narration(
        scenes=scenes_dicts,
        job_id=job_id,
        voice=channel.voice,
        rate=channel.tts_rate,
    )

    # ── Step 2: Calcular Durações Reais ───────────────────────
    audio_durations = [get_audio_duration(p) for p in audio_paths]
    total_duration = sum(audio_durations)
    logger.info(f"⏱️ [VideoEditor] Total narration duration: {total_duration:.1f}s")

    # Atualiza duração das cenas com base no áudio real
    for i, scene_dict in enumerate(scenes_dicts):
        if i < len(audio_durations):
            scene_dict["duration_s"] = audio_durations[i]

    # ── Step 3: Baixar B-roll ──────────────────────────────────
    logger.info("📹 [VideoEditor] Fetching B-roll clips...")
    broll_paths = fetch_all_broll(scenes_dicts, job_id)

    # ── Step 4: Gerar Legendas ─────────────────────────────────
    subtitle_path = str(job_audio_path(job_id) / "subtitles.srt")
    logger.info("📝 [VideoEditor] Generating Hormozi-style subtitles...")
    generate_hormozi_subtitles(
        scenes=scenes_dicts,
        audio_durations=audio_durations,
        output_path=subtitle_path,
    )

    # ── Step 5: Montar Vídeo Final ─────────────────────────────
    logger.info("🎬 [VideoEditor] Assembling final video...")
    final_video_path = assemble_video(
        scenes=scenes_dicts,
        audio_paths=audio_paths,
        broll_paths=broll_paths,
        subtitle_path=subtitle_path,
        job_id=job_id,
    )

    # ── Step 6: Limpar arquivos temporários ───────────────────
    cleanup_temp_files(job_id)

    # ── Step 7: Construir VideoData ───────────────────────────
    final_path = Path(final_video_path)
    file_size_mb = final_path.stat().st_size / (1024 * 1024) if final_path.exists() else 0

    # Concatena todos os áudios (caminho do mp3 da narração completa)
    full_audio_path = str(job_audio_path(job_id) / "narration_full.mp3")

    video_data = VideoData(
        job_id=job_id,
        script_data=script_data,
        video_path=final_video_path,
        audio_path=full_audio_path,
        subtitle_path=subtitle_path,
        actual_duration_s=total_duration,
        file_size_mb=round(file_size_mb, 2),
        resolution=f"{1920}x{1080}",
    )

    logger.info(
        f"✅ [VideoEditor] Done | video={final_video_path} | "
        f"{file_size_mb:.1f}MB | {total_duration:.0f}s"
    )

    # ── Step 8: Aguardar Designer e enfileirar upload ──────────
    # O Manager Agent aguarda tanto o VideoData quanto o ThumbnailData.
    # Aqui salvamos o VideoData em Redis para o Manager consolidar.
    _store_video_data(video_data)

    logger.info(f"→ [VideoEditor] VideoData stored, waiting for Designer to complete")
    return video_data


def _store_video_data(video_data: VideoData) -> None:
    """
    Salva VideoData no Redis para o Manager Agent consolidar com ThumbnailData.
    Usa Redis diretamente (não fila) como storage temporário.
    """
    import redis
    import json

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)

    key = f"video_data:{video_data.job_id}"
    r.setex(
        key,
        86400,  # 24h TTL
        json.dumps(video_data.model_dump(mode="json"), default=str)
    )
    logger.debug(f"VideoData stored in Redis key: {key}")
