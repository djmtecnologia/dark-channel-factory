"""
Omnichannel Shorts Agent — Dark Channel Factory v2
Módulo 8: Extrai 3 clipes 9:16 do vídeo longo e publica em:
- YouTube Shorts (via API)
- TikTok (via Playwright stealth)
- Instagram Reels (via Playwright stealth)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from shared.logger import get_logger
from shared.models import VideoData, ScriptData
from shared.models_v2 import ShortClip, ShortData, ShortPlatform
from shared.storage import job_video_path

load_dotenv()
logger = get_logger(__name__)

SKIP_UPLOAD = os.getenv("SKIP_UPLOAD", "false").lower() == "true"
TIKTOK_COOKIES_FILE = os.getenv("TIKTOK_COOKIES_FILE", "credentials/tiktok_cookies.json")
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE", "credentials/instagram_cookies.json")

# Configurações de clipes
SHORTS_DURATION_S = 55          # Máximo 60s, usamos 55 para segurança
SHORTS_COUNT = 3                 # Número de clipes por vídeo
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920


# ─────────────────────────────────────────────────────────────
# Extrator de Clipes
# ─────────────────────────────────────────────────────────────

def extract_short_clips(
    video_data: VideoData,
    script_data: ScriptData,
    job_id: UUID,
) -> list[ShortClip]:
    """
    Extrai 3 clipes verticais 9:16 do vídeo longo via FFmpeg.
    Estratégia de extração:
    - Clip 1: Início (hook scene)
    - Clip 2: Meio (melhor cena de conteúdo)
    - Clip 3: Final (CTA)
    """
    source_path = video_data.video_path
    if not Path(source_path).exists():
        raise FileNotFoundError(f"Source video not found: {source_path}")

    total_duration = video_data.actual_duration_s
    clips: list[ShortClip] = []

    # Calcula pontos de extração
    extraction_points = _calculate_extraction_points(
        total_duration, script_data.scenes
    )

    clips_dir = job_video_path(job_id) / "shorts"
    clips_dir.mkdir(parents=True, exist_ok=True)

    for i, (start_s, hook_text, cta_text) in enumerate(extraction_points, 1):
        output_path = str(clips_dir / f"short_{i:02d}.mp4")

        logger.info(f"✂️ [Shorts] Extracting clip {i}/{SHORTS_COUNT} at {start_s:.1f}s")

        success = _extract_and_process_clip(
            source_path=source_path,
            output_path=output_path,
            start_s=start_s,
            duration_s=SHORTS_DURATION_S,
            hook_text=hook_text,
            cta_text=cta_text,
        )

        if success:
            clips.append(ShortClip(
                clip_index=i,
                source_video_path=source_path,
                output_path=output_path,
                start_s=start_s,
                end_s=start_s + SHORTS_DURATION_S,
                duration_s=SHORTS_DURATION_S,
                hook_text=hook_text,
                cta_text=cta_text,
            ))

    logger.info(f"✅ [Shorts] {len(clips)}/{SHORTS_COUNT} clips extracted")
    return clips


def _calculate_extraction_points(
    total_duration: float,
    scenes: list,
) -> list[tuple[float, str, str]]:
    """
    Calcula os melhores pontos de extração.
    Retorna lista de (start_s, hook_text, cta_text).
    """
    points: list[tuple[float, str, str]] = []
    safe_duration = total_duration - SHORTS_DURATION_S - 5

    if safe_duration <= 0:
        logger.warning("[Shorts] Video too short for clips")
        return []

    # Usa cenas se disponíveis
    if scenes and len(scenes) >= 3:
        scene_objects = list(scenes)

        # Clip 1: Cena de hook (inicio)
        start1 = 0.0
        hook1 = scene_objects[0].text[:50] if scene_objects else "Watch till the end"
        points.append((start1, hook1, "Full video in the description ↑"))

        # Clip 2: Meio do vídeo (melhor conteúdo)
        mid_index = len(scene_objects) // 2
        start2 = sum(s.duration_s for s in scene_objects[:mid_index])
        start2 = min(start2, safe_duration)
        hook2 = scene_objects[mid_index].text[:50] if mid_index < len(scene_objects) else "Did you know this?"
        points.append((start2, hook2, "Link in bio for the full guide ↑"))

        # Clip 3: CTA final
        start3 = max(0, total_duration - SHORTS_DURATION_S - 10)
        hook3 = "Most people don't know this..."
        points.append((start3, hook3, "Subscribe for more AI tips!"))

    else:
        # Fallback: distribuição equidistante
        interval = safe_duration / SHORTS_COUNT
        hooks = [
            "Most people don't know this...",
            "This changed everything for me",
            "The #1 mistake everyone makes",
        ]
        ctas = [
            "Full video in description ↑",
            "Link in bio for more ↑",
            "Subscribe for AI tips!",
        ]
        for i in range(SHORTS_COUNT):
            points.append((i * interval, hooks[i], ctas[i]))

    return points


def _extract_and_process_clip(
    source_path: str,
    output_path: str,
    start_s: float,
    duration_s: float,
    hook_text: str,
    cta_text: str,
) -> bool:
    """
    Extrai e processa um clipe para formato 9:16 com FFmpeg.
    Adiciona:
    - Crop vertical centralizado
    - Texto de hook (primeiros 3s)
    - Legenda dinâmica centralizada
    - CTA overlay final (últimos 3s)
    """
    # Sanitiza textos para FFmpeg drawtext
    hook_safe = hook_text.replace("'", "").replace(":", "")[:40]
    cta_safe = cta_text.replace("'", "").replace(":", "")[:40]

    # Filtros FFmpeg
    vf_filters = [
        # 1. Corta para 9:16 centralizado
        f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0",
        # 2. Redimensiona para 1080x1920
        f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}",
        # 3. Hook text (primeiros 3 segundos)
        (
            f"drawtext=text='{hook_safe}'"
            f":fontsize=60:fontcolor=white:borderw=3:bordercolor=black"
            f":x=(w-text_w)/2:y=h*0.15"
            f":enable='lt(t,3)'"
        ),
        # 4. CTA text (últimos 3 segundos)
        (
            f"drawtext=text='{cta_safe}'"
            f":fontsize=50:fontcolor=yellow:borderw=3:bordercolor=black"
            f":x=(w-text_w)/2:y=h*0.85"
            f":enable='gte(t,{duration_s - 3})'"
        ),
        # 5. Overlays decorativos (barra colorida no topo)
        "drawbox=x=0:y=0:w=iw:h=8:color=#FF6B35@0.9:t=fill",
    ]

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-i", source_path,
        "-t", str(duration_s),
        "-vf", ",".join(vf_filters),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        logger.info(f"✓ Clip extracted: {Path(output_path).name} ({size_mb:.1f}MB)")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg clip extraction failed: {e.stderr.decode()[:300]}")
        return False
    except Exception as e:
        logger.error(f"Clip extraction error: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Publisher: YouTube Shorts
# ─────────────────────────────────────────────────────────────

def publish_to_youtube_shorts(
    clip: ShortClip,
    title: str,
    description: str,
    tags: list[str],
    parent_video_id: str,
) -> str | None:
    """Upload para YouTube Shorts via Data API v3."""
    if SKIP_UPLOAD:
        mock_id = f"MOCK_SHORT_{clip.clip_index}"
        logger.info(f"[SKIP_UPLOAD] Would upload Short {clip.clip_index} → {mock_id}")
        return mock_id

    try:
        from agents.manager_analyst.youtube_uploader import _build_youtube_service
        from googleapiclient.http import MediaFileUpload

        youtube = _build_youtube_service()

        # Título otimizado para Shorts (max 100 chars)
        short_title = f"{clip.hook_text[:50]} #Shorts"

        body = {
            "snippet": {
                "title": short_title[:100],
                "description": f"{description[:400]}\n\n📺 Full video: https://youtu.be/{parent_video_id}\n\n{clip.cta_text}",
                "tags": tags[:10] + ["shorts", "youtubeshorts"],
                "categoryId": "28",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }

        media = MediaFileUpload(clip.output_path, mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response.get("id")
        logger.info(f"✅ [Shorts] YouTube Short published: {video_id}")
        return video_id

    except Exception as e:
        logger.error(f"YouTube Shorts upload failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Publisher: TikTok & Instagram via Playwright (Stealth)
# ─────────────────────────────────────────────────────────────

def publish_to_tiktok_stealth(
    clip: ShortClip,
    caption: str,
    affiliate_link: str = "",
) -> str | None:
    """
    Upload para TikTok via Playwright com persistência de sessão.
    Requer: credentials/tiktok_cookies.json com sessão autenticada.
    """
    import asyncio

    async def _upload():
        try:
            from playwright.async_api import async_playwright

            cookies_path = Path(TIKTOK_COOKIES_FILE)
            if not cookies_path.exists():
                logger.warning(f"[TikTok] No cookies found at {cookies_path}. Please export from browser.")
                return None

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 768},
                )

                # Carrega cookies de sessão salva
                with open(cookies_path) as f:
                    cookies = json.load(f)
                await context.add_cookies(cookies)

                page = await context.new_page()
                await page.goto("https://www.tiktok.com/upload", wait_until="networkidle")
                await asyncio.sleep(3)

                # Upload do arquivo
                file_input = await page.wait_for_selector("input[type='file']", timeout=15000)
                await file_input.set_input_files(clip.output_path)
                await asyncio.sleep(8)  # Aguarda processamento

                # Preenche legenda com affiliate link
                full_caption = f"{caption[:100]}\n\n{affiliate_link}"[:300]
                caption_box = page.locator('[data-text="true"]').first
                if await caption_box.is_visible():
                    await caption_box.fill(full_caption)

                # Publica
                await asyncio.sleep(2)
                post_btn = page.locator('button:has-text("Post"), button:has-text("Publish")').first
                await post_btn.click()
                await asyncio.sleep(5)

                logger.info(f"✅ [TikTok] Clip {clip.clip_index} uploaded")
                await browser.close()
                return f"tiktok_clip_{clip.clip_index}"

        except Exception as e:
            logger.error(f"TikTok upload failed: {e}")
            return None

    if SKIP_UPLOAD:
        logger.info(f"[SKIP_UPLOAD] Would upload to TikTok clip {clip.clip_index}")
        return f"MOCK_TIKTOK_{clip.clip_index}"

    return asyncio.run(_upload())


def publish_to_instagram_stealth(
    clip: ShortClip,
    caption: str,
    affiliate_link: str = "",
) -> str | None:
    """
    Upload para Instagram Reels via Playwright com persistência de sessão.
    """
    if SKIP_UPLOAD:
        logger.info(f"[SKIP_UPLOAD] Would upload to Instagram Reel {clip.clip_index}")
        return f"MOCK_IG_{clip.clip_index}"

    logger.info(f"[Instagram] Reel upload not yet configured — add credentials/instagram_cookies.json")
    return None


# ─────────────────────────────────────────────────────────────
# Main Shorts Agent
# ─────────────────────────────────────────────────────────────

def run_shorts_agent(
    job_id_str: str,
    video_data_dict: dict,
    script_data_dict: dict,
    youtube_video_id: str,
) -> ShortData:
    """
    Executa o Omnichannel Shorts Agent.

    Args:
        job_id_str: UUID do job
        video_data_dict: VideoData serializado
        script_data_dict: ScriptData serializado (para legenda e tags)
        youtube_video_id: ID do vídeo longo já publicado
    """
    from shared.models import VideoData, ScriptData

    video_data = VideoData(**video_data_dict)
    script_data = ScriptData(**script_data_dict)
    job_id = UUID(job_id_str)

    logger.info(f"📱 [Shorts] Starting | job={job_id_str[:8]} | parent={youtube_video_id}")

    # ── Step 1: Extrair clipes ─────────────────────────────────
    clips = extract_short_clips(video_data, script_data, job_id)

    if not clips:
        logger.error("[Shorts] No clips extracted")
        return ShortData(job_id=job_id, parent_youtube_video_id=youtube_video_id)

    # ── Step 2: Publicar em cada plataforma ───────────────────
    platforms_published: list[ShortPlatform] = []

    # Prepara textos
    short_description = f"{script_data.title}\n\n{script_data.description[:200]}"
    affiliate_cta = ""
    if script_data.affiliate_links:
        first = script_data.affiliate_links[0]
        affiliate_cta = f"🔗 {first.product_name}: {first.url}"

    for clip in clips:
        # YouTube Shorts
        yt_id = publish_to_youtube_shorts(
            clip=clip,
            title=script_data.title,
            description=short_description,
            tags=script_data.tags,
            parent_video_id=youtube_video_id,
        )
        if yt_id:
            clip.platform_video_ids["youtube_shorts"] = yt_id
            if ShortPlatform.YOUTUBE_SHORTS not in platforms_published:
                platforms_published.append(ShortPlatform.YOUTUBE_SHORTS)

        time.sleep(2)  # Respeita rate limits

        # TikTok
        tk_id = publish_to_tiktok_stealth(
            clip=clip,
            caption=f"{clip.hook_text} | {script_data.title[:50]}",
            affiliate_link=affiliate_cta,
        )
        if tk_id:
            clip.platform_video_ids["tiktok"] = tk_id
            if ShortPlatform.TIKTOK not in platforms_published:
                platforms_published.append(ShortPlatform.TIKTOK)

        time.sleep(2)

        # Instagram
        ig_id = publish_to_instagram_stealth(
            clip=clip,
            caption=f"{clip.hook_text}\n\n{affiliate_cta}",
        )
        if ig_id:
            clip.platform_video_ids["instagram_reels"] = ig_id
            if ShortPlatform.INSTAGRAM_REELS not in platforms_published:
                platforms_published.append(ShortPlatform.INSTAGRAM_REELS)

    short_data = ShortData(
        job_id=job_id,
        parent_youtube_video_id=youtube_video_id,
        parent_title=script_data.title,
        clips=clips,
        platforms_published=platforms_published,
    )

    logger.info(
        f"✅ [Shorts] Complete | clips={len(clips)} | "
        f"platforms={[p.value for p in platforms_published]}"
    )
    return short_data
