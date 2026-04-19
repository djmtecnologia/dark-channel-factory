"""
TTS Engine — Video Editor Agent
Wrapper assíncrono para edge-tts (Azure Neural Voices, gratuito e ilimitado).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

import edge_tts  # type: ignore

from shared.logger import get_logger
from shared.storage import job_audio_path

logger = get_logger(__name__)

# Vozes de alta retenção recomendadas por idioma/estilo
RECOMMENDED_VOICES = {
    "en-US": {
        "female_professional": "en-US-AriaNeural",
        "male_professional": "en-US-GuyNeural",
        "female_energetic": "en-US-JennyNeural",
        "male_energetic": "en-US-EricNeural",
    },
    "pt-BR": {
        "female_professional": "pt-BR-FranciscaNeural",
        "male_professional": "pt-BR-AntonioNeural",
    },
    "es-ES": {
        "female_professional": "es-ES-ElviraNeural",
        "male_professional": "es-ES-AlvaroNeural",
    },
}


async def _generate_tts_async(
    text: str,
    voice: str,
    output_path: str,
    rate: str = "+15%",
    volume: str = "+10%",
    pitch: str = "-5Hz",
) -> str:
    """Gera áudio TTS de forma assíncrona."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
    )
    await communicate.save(output_path)
    return output_path


def generate_scene_audio(
    scene_id: int,
    text: str,
    job_id: UUID,
    voice: str = "en-US-AriaNeural",
    rate: str = "+15%",
    volume: str = "+10%",
) -> str:
    """
    Gera áudio para uma cena específica.
    Retorna o caminho do arquivo .mp3 gerado.
    """
    audio_dir = job_audio_path(job_id)
    output_path = str(audio_dir / f"scene_{scene_id:03d}.mp3")

    # Limpa texto para TTS (remove caracteres problemáticos)
    clean_text = _clean_text_for_tts(text)

    asyncio.run(_generate_tts_async(
        text=clean_text,
        voice=voice,
        output_path=output_path,
        rate=rate,
        volume=volume,
    ))

    size_kb = Path(output_path).stat().st_size // 1024
    logger.debug(f"🎤 TTS scene_{scene_id:03d} | voice={voice} | {size_kb}KB")
    return output_path


def generate_full_narration(
    scenes: list[dict],
    job_id: UUID,
    voice: str = "en-US-AriaNeural",
    rate: str = "+15%",
) -> list[str]:
    """
    Gera áudio para todas as cenas de um script.
    Retorna lista de caminhos .mp3 na ordem das cenas.
    """
    audio_paths: list[str] = []

    logger.info(f"🎤 Generating TTS for {len(scenes)} scenes | voice={voice}")

    for scene in scenes:
        try:
            path = generate_scene_audio(
                scene_id=scene["id"],
                text=scene["text"],
                job_id=job_id,
                voice=voice,
                rate=rate,
            )
            audio_paths.append(path)
        except Exception as e:
            logger.error(f"TTS failed for scene {scene.get('id', '?')}: {e}")
            raise

    logger.info(f"✅ TTS complete | {len(audio_paths)} audio files generated")
    return audio_paths


def _clean_text_for_tts(text: str) -> str:
    """Remove caracteres que podem causar problemas no TTS."""
    import re
    # Remove markdown symbols
    text = re.sub(r"[*_`#>\[\]()]", "", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove emojis
    text = re.sub(r"[^\x00-\x7F]+", "", text)
    # Colapsa espaços múltiplos
    text = " ".join(text.split())
    return text.strip()


async def list_available_voices(language: str = "en-US") -> list[dict]:
    """Lista vozes disponíveis para um idioma."""
    voices = await edge_tts.list_voices()
    return [v for v in voices if v.get("Locale", "").startswith(language)]
