"""
Video Assembler — Video Editor Agent
Pipeline FFmpeg para montar vídeo final: B-roll + Narração + Legendas + Música.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from shared.logger import get_logger
from shared.storage import job_audio_path, job_video_path

logger = get_logger(__name__)

# Configurações de vídeo
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_BITRATE = "4000k"
AUDIO_BITRATE = "192k"

# Configurações de subtítulo (estilo Hormozi)
SUBTITLE_FONT = "Arial-Bold"
SUBTITLE_FONT_SIZE = 75
SUBTITLE_PRIMARY_COLOR = "&H00FFFFFF"   # Branco
SUBTITLE_OUTLINE_COLOR = "&H00000000"   # Preto
SUBTITLE_SHADOW_COLOR = "&H80000000"    # Sombra semi-transparente
SUBTITLE_OUTLINE_WIDTH = 4
SUBTITLE_MARGIN_V = 100                  # Margem vertical (posição)


def _check_ffmpeg() -> bool:
    """Verifica se FFmpeg está instalado."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _create_background_color_video(
    duration_s: float,
    output_path: str,
    color: str = "0x1a1a2e",
) -> str:
    """Cria vídeo de fundo colorido (fallback quando não há B-roll)."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:size={VIDEO_WIDTH}x{VIDEO_HEIGHT}:rate={VIDEO_FPS}:duration={duration_s}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return output_path


def _prepare_broll_clip(
    input_path: str | None,
    output_path: str,
    duration_s: float,
    fallback_color: str = "0x1a1a2e",
) -> str:
    """Prepara clipe B-roll: redimensiona para 1080p e corta para duração."""
    if not input_path or not Path(input_path).exists():
        return _create_background_color_video(duration_s, output_path, fallback_color)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(duration_s),
        "-vf", (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"setsar=1"
        ),
        "-r", str(VIDEO_FPS),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-an",  # Remove áudio do B-roll
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.warning(f"B-roll prep failed for {input_path}: {e.stderr.decode()[:200]}")
        return _create_background_color_video(duration_s, output_path, fallback_color)


def _build_concat_file(video_paths: list[str], concat_file_path: str) -> None:
    """Cria arquivo de concatenação para FFmpeg."""
    with open(concat_file_path, "w") as f:
        for path in video_paths:
            # Normaliza paths para FFmpeg (barras forward)
            normalized = path.replace("\\", "/")
            f.write(f"file '{normalized}'\n")


def assemble_video(
    scenes: list[dict],
    audio_paths: list[str],
    broll_paths: dict[int, str | None],
    subtitle_path: str | None,
    job_id: UUID,
    output_filename: str | None = None,
) -> str:
    """
    Monta o vídeo final via FFmpeg.

    Pipeline:
    1. Prepara clipes B-roll (resize + trim)
    2. Concatena B-rolls
    3. Concatena áudios de narração
    4. Combina vídeo + áudio
    5. Aplica legendas (se disponível)
    6. Renderiza em H.264 1080p

    Returns:
        Caminho do arquivo .mp4 final
    """
    if not _check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found! Install from https://ffmpeg.org/download.html "
            "or run: winget install FFmpeg"
        )

    video_dir = job_video_path(job_id)
    audio_dir = job_audio_path(job_id)
    temp_dir = video_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    output_filename = output_filename or f"{job_id}_final.mp4"
    final_output = str(video_dir / output_filename)

    logger.info(f"🎬 [Assembler] Starting assembly for job={job_id} | scenes={len(scenes)}")

    # ── Step 1: Preparar clipes B-roll ────────────────────────────
    prepared_broll: list[str] = []
    for i, scene in enumerate(scenes):
        scene_id = scene["id"]
        audio_path = audio_paths[i] if i < len(audio_paths) else None
        duration_s = scene.get("duration_s", 30.0)

        # Calcula duração real do áudio se disponível
        if audio_path and Path(audio_path).exists():
            from agents.video_editor.subtitle_generator import get_audio_duration
            duration_s = get_audio_duration(audio_path)

        broll_input = broll_paths.get(scene_id)
        broll_output = str(temp_dir / f"broll_{i:03d}.mp4")

        logger.debug(f"Preparing B-roll scene_{scene_id} | duration={duration_s:.1f}s")
        _prepare_broll_clip(broll_input, broll_output, duration_s)
        prepared_broll.append(broll_output)

    # ── Step 2: Concatenar B-rolls ────────────────────────────────
    concat_video_file = str(temp_dir / "broll_list.txt")
    _build_concat_file(prepared_broll, concat_video_file)

    concat_video_output = str(temp_dir / "combined_video.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_video_file,
        "-c", "copy",
        concat_video_output,
    ], check=True, capture_output=True, timeout=600)

    logger.info("✓ [Assembler] B-rolls concatenated")

    # ── Step 3: Concatenar Áudios de Narração ────────────────────
    concat_audio_file = str(temp_dir / "audio_list.txt")
    valid_audio = [p for p in audio_paths if p and Path(p).exists()]
    _build_concat_file(valid_audio, concat_audio_file)

    concat_audio_output = str(temp_dir / "combined_narration.mp3")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_audio_file,
        "-c", "copy",
        concat_audio_output,
    ], check=True, capture_output=True, timeout=300)

    logger.info("✓ [Assembler] Narrations concatenated")

    # ── Step 4+5: Combinar vídeo + áudio + legendas ──────────────
    vf_filters = []

    # Escurecimento do fundo para contraste com legendas
    vf_filters.append("eq=brightness=-0.1:saturation=1.2")

    # Legendas via ASS embed (se disponível)
    if subtitle_path and Path(subtitle_path).exists():
        # Converte SRT para ASS para estilização avançada
        ass_path = str(temp_dir / "subtitles.ass")
        _convert_srt_to_ass(subtitle_path, ass_path)
        if Path(ass_path).exists():
            ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
            vf_filters.append(f"ass={ass_escaped}")

    # Filtro de vídeo composto
    vf_str = ",".join(vf_filters) if vf_filters else "null"

    # Determina se tem música de fundo
    # (por padrão usa apenas a narração — música pode ser adicionada depois)
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", concat_video_output,
        "-i", concat_audio_output,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-b:v", VIDEO_BITRATE,
        "-vf", vf_str,
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-shortest",  # Corta para a duração mais curta (vídeo ou áudio)
        "-movflags", "+faststart",  # Otimizado para streaming/YouTube
        "-map", "0:v:0",
        "-map", "1:a:0",
        final_output,
    ]

    logger.info("🎬 [Assembler] Rendering final video...")
    subprocess.run(ffmpeg_cmd, check=True, capture_output=True, timeout=1800)

    # Verifica output
    output_path_obj = Path(final_output)
    if not output_path_obj.exists():
        raise RuntimeError(f"Video assembly failed — output not found: {final_output}")

    size_mb = output_path_obj.stat().st_size / (1024 * 1024)
    logger.info(f"✅ [Assembler] Video ready: {final_output} | {size_mb:.1f}MB")

    return final_output


def _convert_srt_to_ass(srt_path: str, ass_path: str) -> None:
    """Converte SRT para ASS com estilo Hormozi (maiúsculas, bold, centralizado)."""
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", srt_path,
            ass_path,
        ], check=True, capture_output=True, timeout=30)

        # Aplica estilo personalizado ao ASS
        if Path(ass_path).exists():
            _apply_hormozi_style(ass_path)
    except Exception as e:
        logger.warning(f"SRT→ASS conversion failed: {e}")


def _apply_hormozi_style(ass_path: str) -> None:
    """Modifica o arquivo ASS para aplicar estilo Hormozi."""
    try:
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Substitui a linha de estilo padrão
        hormozi_style = (
            f"Style: Default,{SUBTITLE_FONT},{SUBTITLE_FONT_SIZE},"
            f"{SUBTITLE_PRIMARY_COLOR},{SUBTITLE_PRIMARY_COLOR},"
            f"{SUBTITLE_OUTLINE_COLOR},{SUBTITLE_SHADOW_COLOR},"
            f"1,0,0,0,100,100,0,0,3,{SUBTITLE_OUTLINE_WIDTH},1,2,10,10,{SUBTITLE_MARGIN_V},1"
        )

        import re
        content = re.sub(r"Style: Default,[^\n]+", hormozi_style, content)

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"Hormozi style application failed: {e}")
