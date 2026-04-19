"""
Thumbnail Generator — Designer Agent
Gera 3 variantes de thumbnail via DALL-E 3 para teste A/B/C.
Fallback: gera thumbnail com Pillow (sem API) quando DALL-E indisponível.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.storage import job_thumbnail_path

load_dotenv()
logger = get_logger(__name__)


def generate_thumbnail_variants(
    job_id: UUID,
    title: str,
    thumbnail_brief: str,
    dominant_color: str = "#FF6B35",
    primary_keyword: str = "AI tools",
    variant_prompts: dict | None = None,
) -> list[dict]:
    """
    Gera 3 variantes de thumbnail.
    Tenta DALL-E 3 primeiro, fallback para Pillow.

    Returns:
        Lista de dicts com variant, path, prompt_used
    """
    thumb_dir = job_thumbnail_path(job_id)
    variants_output: list[dict] = []

    llm = get_llm_client()

    # Se prompts já foram gerados pelo LLM, usa eles; senão gera defaults
    if not variant_prompts:
        variant_prompts = _create_default_prompts(title, thumbnail_brief, dominant_color)

    for variant_letter, prompt_data in [
        ("A", variant_prompts.get("variant_a", {})),
        ("B", variant_prompts.get("variant_b", {})),
        ("C", variant_prompts.get("variant_c", {})),
    ]:
        output_path = str(thumb_dir / f"thumbnail_{variant_letter}.png")
        prompt = prompt_data.get("prompt", "")
        text_overlay = prompt_data.get("text_overlay", title[:20])

        success = False

        # Tenta DALL-E 3
        if prompt and llm._openai_client:
            image_bytes = llm.generate_image(
                prompt=f"{prompt}\n\nIMPORTANT: This is for YouTube thumbnail (1792x1024).",
                size="1792x1024",
            )
            if image_bytes:
                with open(output_path, "wb") as f:
                    f.write(image_bytes)
                logger.info(f"🎨 [Designer] Thumbnail {variant_letter} generated via DALL-E 3")
                success = True

        # Fallback: Pillow (sem custo)
        if not success:
            _generate_pillow_thumbnail(
                output_path=output_path,
                title=title,
                text_overlay=text_overlay,
                dominant_color=dominant_color,
                variant_letter=variant_letter,
            )
            logger.info(f"🎨 [Designer] Thumbnail {variant_letter} generated via Pillow (fallback)")
            prompt = f"Pillow-generated: {text_overlay}"

        variants_output.append({
            "variant": variant_letter,
            "path": output_path,
            "prompt_used": prompt[:200],
            "is_active": variant_letter == "A",
        })

    return variants_output


def _generate_pillow_thumbnail(
    output_path: str,
    title: str,
    text_overlay: str,
    dominant_color: str,
    variant_letter: str,
) -> None:
    """
    Gera thumbnail com Pillow quando DALL-E não está disponível.
    Design minimalista mas profissional.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        import math

        width, height = 1280, 720

        # Paletas por variante
        palettes = {
            "A": {"bg": "#0D1117", "accent": "#FF6B35", "text": "#FFFFFF"},
            "B": {"bg": "#1a1a2e", "accent": "#00D4AA", "text": "#FFFFFF"},
            "C": {"bg": "#16213E", "accent": "#F7B731", "text": "#FFFFFF"},
        }
        palette = palettes.get(variant_letter, palettes["A"])

        # Parse hex colors
        def hex_to_rgb(hex_color: str) -> tuple:
            hex_color = hex_color.lstrip("#")
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        bg_color = hex_to_rgb(palette["bg"])
        accent_color = hex_to_rgb(palette["accent"])
        text_color = hex_to_rgb(palette["text"])

        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # Gradiente diagonal de fundo
        for x in range(width):
            for y in range(height):
                factor = (x + y) / (width + height)
                r = int(bg_color[0] + (accent_color[0] - bg_color[0]) * factor * 0.3)
                g = int(bg_color[1] + (accent_color[1] - bg_color[1]) * factor * 0.3)
                b = int(bg_color[2] + (accent_color[2] - bg_color[2]) * factor * 0.3)
                draw.point((x, y), fill=(r, g, b))

        # Linha accent decorativa
        draw.rectangle([0, height * 0.7, width, height * 0.73], fill=accent_color)

        # Tenta carregar fonte
        try:
            # Tenta fontes disponíveis no Windows/Linux
            for font_path in [
                "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
            ]:
                if Path(font_path).exists():
                    font_title_size = 72
                    font_overlay_size = 90
                    font_title = ImageFont.truetype(font_path, font_title_size)
                    font_overlay = ImageFont.truetype(font_path, font_overlay_size)
                    break
            else:
                raise Exception("No font found")
        except Exception:
            font_title = ImageFont.load_default()
            font_overlay = font_title

        # Texto overlay principal (grande, em cima)
        overlay_text = text_overlay.upper()
        draw.text((width//2, height * 0.35), overlay_text,
                  font=font_overlay, fill=accent_color, anchor="mm",
                  stroke_width=3, stroke_fill=(0, 0, 0))

        # Título do vídeo (menor, embaixo)
        title_words = title[:50]
        draw.text((width//2, height * 0.65), title_words,
                  font=font_title, fill=text_color, anchor="mm",
                  stroke_width=2, stroke_fill=(0, 0, 0))

        # Marker de canal fictício no canto
        draw.text((40, 40), "▶ AIToolsUnlocked",
                  font=ImageFont.load_default(), fill=accent_color)

        img.save(output_path, "PNG", optimize=True)

    except ImportError:
        logger.error("Pillow not installed — thumbnail generation failed")
        raise
    except Exception as e:
        logger.error(f"Pillow thumbnail generation failed: {e}")
        raise


def _create_default_prompts(title: str, brief: str, color: str) -> dict:
    """Cria prompts padrão quando o LLM não gerou."""
    return {
        "variant_a": {
            "style": "face_with_text",
            "prompt": (
                f"YouTube thumbnail, ultra realistic, 4K quality. "
                f"Dark background ({color} gradient). "
                f"A shocked/amazed professional person on the left side looking at camera. "
                f"Bold white text '{title[:25]}' on the right side. "
                f"Dramatic studio lighting. Tech/business aesthetic."
            ),
            "text_overlay": title[:20],
        },
        "variant_b": {
            "style": "data_visualization",
            "prompt": (
                f"YouTube thumbnail infographic style. "
                f"Dark navy background. Green glowing chart showing exponential growth. "
                f"Bold text overlay. Futuristic AI aesthetic. High contrast."
            ),
            "text_overlay": "AI TOOLS 2025",
        },
        "variant_c": {
            "style": "comparison",
            "prompt": (
                f"YouTube thumbnail split screen. "
                f"Left: messy, stressed person symbol. "
                f"Right: clean, successful symbol with AI. "
                f"Bold 'BEFORE vs AFTER' text. High contrast red and green."
            ),
            "text_overlay": "BEFORE vs AFTER",
        },
    }
