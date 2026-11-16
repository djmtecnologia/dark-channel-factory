"""
Media Fetcher — Video Editor Agent
Busca clipes de vídeo de fundo via Pexels API (gratuito, royalty-free).
Fallback: Pixabay API.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path
from uuid import UUID

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logger import get_logger
from shared.storage import job_video_path

load_dotenv()
logger = get_logger(__name__)

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

PEXELS_HEADERS = {
    "Authorization": PEXELS_API_KEY,
    "User-Agent": "DarkChannelFactory/1.0",
}

# Queries padrão por tema (fallbacks se query específica não encontrar)
DEFAULT_QUERIES_BY_THEME = {
    "technology": ["modern office technology", "laptop screen code", "data visualization"],
    "business": ["business meeting success", "entrepreneur working laptop", "modern office"],
    "ai": ["artificial intelligence technology", "robot hand human", "data neural network"],
    "productivity": ["person working efficiently", "time management", "organized workspace"],
    "money": ["financial growth chart", "money success business", "investment growth"],
    "general": ["technology abstract", "professional background", "modern workspace"],
}


def _get_local_cache_clips() -> list[Path]:
    """Retorna lista de clipes disponíveis no cache local."""
    cache_dirs = [
        Path("assets_cache"),
        Path("assets_cache/premium_cache")
    ]
    clips = []
    for d in cache_dirs:
        if d.exists():
            clips.extend(list(d.glob("*.mp4")))
    return clips


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def fetch_video_clip(
    query: str,
    job_id: UUID,
    scene_id: int,
    min_duration_s: int = 10,
    orientation: str = "landscape",
) -> str | None:
    """
    Busca e baixa um clipe de vídeo de fundo.
    Retorna caminho do arquivo .mp4 baixado, ou None se falhar.
    """
    video_dir = job_video_path(job_id) / "b_roll"
    video_dir.mkdir(parents=True, exist_ok=True)

    output_path = video_dir / f"scene_{scene_id:03d}_broll.mp4"
    if output_path.exists() and output_path.stat().st_size > 0:
        logger.info(f"♻️ Reusing matrix B-roll for scene_{scene_id}: {output_path}")
        return str(output_path)

    # ── Step 0: Tentar Cache Local ─────────────────────────────
    local_clips = _get_local_cache_clips()
    if local_clips:
        # Tenta encontrar um clipe que combine com a query (busca simples por nome)
        keywords = query.lower().split()
        matches = [c for c in local_clips if any(k in c.name.lower() for k in keywords)]
        
        selected_clip = random.choice(matches) if matches else random.choice(local_clips)
        
        # Copia o clipe para a pasta do job
        import shutil
        shutil.copy2(selected_clip, output_path)
        logger.info(f"📦 [Cache] Using local clip for scene_{scene_id}: {selected_clip.name}")
        return str(output_path)

    # Tenta Pexels primeiro
    if PEXELS_API_KEY and PEXELS_API_KEY != "your_pexels_api_key_here":
        result = _fetch_from_pexels(query, min_duration_s, orientation)
        if result:
            _download_file(result, str(output_path))
            logger.debug(f"📹 B-roll scene_{scene_id}: Pexels | query='{query}'")
            return str(output_path)

    # Fallback: Pixabay
    if PIXABAY_API_KEY and PIXABAY_API_KEY != "your_pixabay_api_key_here":
        result = _fetch_from_pixabay(query, min_duration_s)
        if result:
            _download_file(result, str(output_path))
            logger.debug(f"📹 B-roll scene_{scene_id}: Pixabay | query='{query}'")
            return str(output_path)

    # Fallback final: usar query genérico
    fallback_query = _get_fallback_query(query)
    if fallback_query != query:
        logger.warning(f"No video found for '{query}' — trying fallback '{fallback_query}'")
        return fetch_video_clip(fallback_query, job_id, scene_id, min_duration_s, orientation)

    logger.warning(f"No video found for scene_{scene_id} — will use color background")
    return None


def _fetch_from_pexels(query: str, min_duration: int, orientation: str) -> str | None:
    """Busca URL de download do Pexels."""
    try:
        params = {
            "query": query,
            "per_page": 15,
            "orientation": orientation,
            "size": "medium",
        }
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers=PEXELS_HEADERS,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        videos = response.json().get("videos", [])

        # Filtra por duração mínima
        suitable = [v for v in videos if v.get("duration", 0) >= min_duration]
        if not suitable:
            suitable = videos  # Usa qualquer um se não houver suficientemente longos

        if not suitable:
            return None

        video = random.choice(suitable[:5])  # Varia para não repetir

        # Pega o arquivo HD (720p)
        video_files = video.get("video_files", [])
        hd_files = [f for f in video_files if f.get("quality") == "hd"]
        sd_files = [f for f in video_files if f.get("quality") == "sd"]

        files = hd_files or sd_files or video_files
        if not files:
            return None

        return files[0].get("link")

    except Exception as e:
        logger.error(f"Pexels fetch error: {e}")
        return None


def _fetch_from_pixabay(query: str, min_duration: int) -> str | None:
    """Busca URL de download do Pixabay."""
    try:
        params = {
            "key": PIXABAY_API_KEY,
            "q": query,
            "video_type": "film",
            "per_page": 10,
            "min_width": 1280,
        }
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])

        suitable = [h for h in hits if h.get("duration", 0) >= min_duration]
        if not suitable:
            suitable = hits

        if not suitable:
            return None

        video = random.choice(suitable[:5])
        videos = video.get("videos", {})
        # Tenta medium, depois small
        for quality in ["medium", "small", "large"]:
            url = videos.get(quality, {}).get("url")
            if url:
                return url

        return None

    except Exception as e:
        logger.error(f"Pixabay fetch error: {e}")
        return None


def _download_file(url: str, output_path: str) -> None:
    """Baixa um arquivo de URL."""
    response = requests.get(url, timeout=60, stream=True)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


def _get_fallback_query(query: str) -> str:
    """Retorna query de fallback baseado em keywords no query original."""
    query_lower = query.lower()
    for theme, queries in DEFAULT_QUERIES_BY_THEME.items():
        if theme in query_lower:
            return random.choice(queries)
    return random.choice(DEFAULT_QUERIES_BY_THEME["general"])


def fetch_all_broll(scenes: list[dict], job_id: UUID) -> dict[int, str | None]:
    """
    Baixa B-roll para todas as cenas.
    Retorna dict {scene_id: video_path | None}
    """
    results: dict[int, str | None] = {}
    logger.info(f"📹 Fetching B-roll for {len(scenes)} scenes...")

    for scene in scenes:
        scene_id = scene["id"]
        query = scene.get("b_roll_visual") or scene.get("b_roll_query") or "technology office"
        path = fetch_video_clip(query=query, job_id=job_id, scene_id=scene_id)
        results[scene_id] = path
        time.sleep(0.1)  # Menos delay se estiver em cache

    downloaded = sum(1 for p in results.values() if p)
    logger.info(f"✅ B-roll: {downloaded}/{len(scenes)} clips ready")
    return results
