"""
Subtitle Generator — Video Editor Agent
Gera legendas estilo "Alex Hormozi" (3-4 palavras, bold, alta visibilidade).
Usa arquivo .srt para sincronização precisa com TTS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str

    def to_srt(self) -> str:
        return (
            f"{self.index}\n"
            f"{_ms_to_srt_time(self.start_ms)} --> {_ms_to_srt_time(self.end_ms)}\n"
            f"{self.text}\n"
        )


def _ms_to_srt_time(ms: int) -> str:
    """Converte milissegundos para formato SRT HH:MM:SS,mmm"""
    seconds, ms = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def generate_hormozi_subtitles(
    scenes: list[dict],
    audio_durations: list[float],
    output_path: str,
    words_per_cue: int = 3,
) -> str:
    """
    Gera arquivo .srt com legendas estilo Alex Hormozi.
    - 2-4 palavras por cue
    - Distribuição temporal proporcional à duração do áudio

    Args:
        scenes: Lista de dicts de Scene
        audio_durations: Duração de cada cena em segundos
        output_path: Caminho do arquivo .srt de saída
        words_per_cue: Palavras por legenda

    Returns:
        Caminho do arquivo .srt gerado
    """
    cues: list[SubtitleCue] = []
    cue_index = 1
    current_ms = 0

    for scene, duration_s in zip(scenes, audio_durations):
        text = scene.get("text", "")
        words = _clean_and_split(text)

        if not words:
            current_ms += int(duration_s * 1000)
            continue

        # Calcula duração por palavra baseado no áudio real
        total_duration_ms = int(duration_s * 1000)
        ms_per_word = total_duration_ms / max(len(words), 1)

        # Agrupa em cues de N palavras
        for i in range(0, len(words), words_per_cue):
            chunk = words[i:i + words_per_cue]
            chunk_words_count = len(chunk)
            chunk_duration_ms = int(ms_per_word * chunk_words_count)

            # Garante duração mínima de 300ms e máxima de 2000ms por cue
            chunk_duration_ms = max(300, min(2000, chunk_duration_ms))

            # Texto em MAIÚSCULAS para estilo Hormozi
            cue_text = " ".join(chunk).upper()

            cues.append(SubtitleCue(
                index=cue_index,
                start_ms=current_ms,
                end_ms=current_ms + chunk_duration_ms,
                text=cue_text,
            ))

            current_ms += chunk_duration_ms
            cue_index += 1

    # Escreve arquivo .srt
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for cue in cues:
            f.write(cue.to_srt() + "\n")

    return output_path


def _clean_and_split(text: str) -> list[str]:
    """Limpa texto e divide em palavras."""
    # Remove emojis e caracteres especiais
    text = re.sub(r"[^\x00-\x7F]+", "", text)
    # Remove pontuação no meio de palavras
    text = re.sub(r"[\"'()\[\]{}<>]", "", text)
    words = text.split()
    return [w for w in words if w.strip()]


def get_audio_duration(audio_path: str) -> float:
    """Retorna duração de um arquivo de áudio em segundos via mutagen ou ffprobe."""
    try:
        # Tenta mutagen primeiro (mais leve)
        try:
            from mutagen.mp3 import MP3  # type: ignore
            audio = MP3(audio_path)
            return audio.info.length
        except ImportError:
            pass

        # Fallback: ffprobe
        import subprocess
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 30.0  # Duração padrão se falhar
