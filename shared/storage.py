"""
Storage — Dark Channel Factory
Gerencia criação de diretórios e caminhos de output.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()

VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR", "output/videos"))
THUMBNAILS_DIR = Path(os.getenv("THUMBNAILS_DIR", "output/thumbnails"))
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "output/audio"))
LOGS_DIR = Path(os.getenv("LOGS_DIR", "output/logs"))


def ensure_dirs() -> None:
    """Garante que todos os diretórios de output existam."""
    for d in [VIDEOS_DIR, THUMBNAILS_DIR, AUDIO_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def job_video_path(job_id: UUID) -> Path:
    path = VIDEOS_DIR / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_thumbnail_path(job_id: UUID) -> Path:
    path = THUMBNAILS_DIR / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_audio_path(job_id: UUID) -> Path:
    path = AUDIO_DIR / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cleanup_temp_files(job_id: UUID) -> None:
    """Remove arquivos temporários de áudio por cena."""
    audio_dir = job_audio_path(job_id)
    for f in audio_dir.glob("scene_*.mp3"):
        f.unlink(missing_ok=True)
