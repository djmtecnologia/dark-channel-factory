"""Thumbnails A/B/C via OpenRouter API, Hugging Face Inference API e composição Pillow."""
from __future__ import annotations

import base64
import io
import os
import textwrap
from pathlib import Path
from uuid import UUID

import requests
from PIL import Image, ImageDraw, ImageFont
from huggingface_hub import InferenceClient

from shared.logger import get_logger
from shared.storage import job_thumbnail_path

logger = get_logger(__name__)


def _font(size: int):
    candidates = (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _fetch_openrouter_image(
    prompt: str,
    model: str = "black-forest-labs/flux-1-schnell",
    api_key: str | None = None,
) -> bytes | None:
    """Realiza a chamada para a API do OpenRouter para gerar a imagem base das thumbnails."""
    token = api_key or os.getenv("OPENROUTER_API_KEY", "").strip() or None
    if not token:
        return None

    model_name = os.getenv("OPENROUTER_IMAGE_MODEL", model)
    headers = {
        "Authorization": f"Bearer {token}",
        "HTTP-Referer": "https://dark-channel-factory.local",
        "X-Title": "Dark Channel Factory",
        "Content-Type": "application/json",
    }

    # 1. Tenta endpoint padrão /images/generations da OpenRouter
    url_images = "https://openrouter.ai/api/v1/images/generations"
    payload_images = {
        "prompt": prompt,
        "model": model_name,
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
    }

    try:
        response = requests.post(url_images, headers=headers, json=payload_images, timeout=45)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                item = data["data"][0]
                if "b64_json" in item and item["b64_json"]:
                    return base64.b64decode(item["b64_json"])
                elif "url" in item and item["url"]:
                    img_resp = requests.get(item["url"], timeout=30)
                    if img_resp.status_code == 200:
                        return img_resp.content
        logger.debug("[Designer] OpenRouter /images/generations retornou status %s", response.status_code)
    except Exception as exc:
        logger.debug("[Designer] Erro na requisição OpenRouter /images/generations: %s", exc)

    # 2. Fallback via /chat/completions com suporte multimodalidades de imagem
    url_chat = "https://openrouter.ai/api/v1/chat/completions"
    payload_chat = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }

    try:
        response = requests.post(url_chat, headers=headers, json=payload_chat, timeout=45)
        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                images = message.get("images", []) or message.get("image", [])
                if images:
                    img_data = images[0]
                    if isinstance(img_data, str):
                        if img_data.startswith("data:image"):
                            img_data = img_data.split(",", 1)[1]
                        return base64.b64decode(img_data)
    except Exception as exc:
        logger.debug("[Designer] Erro no fallback /chat/completions do OpenRouter: %s", exc)

    return None


def _fetch_hf_image(prompt: str, model: str = "black-forest-labs/FLUX.1-schnell", token: str | None = None) -> bytes | None:
    """Realiza a chamada para a Hugging Face Inference API (gratuita) para gerar a imagem base das thumbnails."""
    # 1. Tenta via InferenceClient do huggingface_hub
    try:
        client = InferenceClient(model=model, token=token)
        generated = client.text_to_image(prompt, width=1280, height=720)
        buf = io.BytesIO()
        generated.convert("RGB").save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception as exc:
        logger.debug("[Designer] InferenceClient falhou: %s. Tentando requisição HTTP REST direta...", exc)

    # 2. Fallback via requisição REST HTTP direta na API de inferência do Hugging Face
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {"inputs": prompt, "parameters": {"width": 1280, "height": 720}}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200 and response.content:
            return response.content
        logger.warning("[Designer] Hugging Face REST API retornou status HTTP %s", response.status_code)
    except Exception as exc:
        logger.warning("[Designer] Erro na requisição HTTP REST da Hugging Face API: %s", exc)

    return None


def _overlay_title(image_bytes: bytes, title: str, output_path: str, accent: str = "#FFD700") -> None:
    """Sobrepõe o texto do título/hook gerado pelo Ollama na imagem 1280x720 usando Pillow."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((1280, 720))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(overlay)
    
    # Degradê escuro no topo para dar legibilidade ao texto
    for y in range(320):
        alpha = int(220 * (1 - y / 320))
        shade.line((0, y, 1280, y), fill=(0, 0, 0, alpha))
        
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)
    
    # Formatação do texto
    clean_title = title.strip().upper()
    words = clean_title.split()
    if len(words) > 8:
        clean_title = " ".join(words[:8])
        
    wrapped_text = "\n".join(textwrap.wrap(clean_title, width=16)[:3])
    
    font_size = 90 if len(clean_title) < 20 else (72 if len(clean_title) < 35 else 56)
    font = _font(font_size)
    
    # Renderizar texto com contorno preto grosso (stroke) e cor de destaque
    draw.multiline_text(
        (640, 50),
        wrapped_text,
        font=font,
        anchor="ma",
        align="center",
        fill=accent if accent else "#FFD700",
        stroke_width=7,
        stroke_fill="black",
        spacing=6,
    )
    
    image.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)


def _gradient_fallback(title: str, output_path: str, accent: str) -> None:
    """Fallback local com fundo gradiente caso a API de imagem não esteja disponível."""
    image = Image.new("RGB", (1280, 720))
    draw = ImageDraw.Draw(image)
    for y in range(720):
        draw.line((0, y, 1280, y), fill=(15 + y // 20, 12, 45 + y // 7))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    _overlay_title(buffer.getvalue(), title, output_path, accent)


def generate_thumbnail_variants(
    job_id: UUID,
    title: str,
    thumbnail_brief: str,
    dominant_color: str,
    primary_keyword: str,
    variant_prompts: dict | list | None = None,
) -> list[dict]:
    """Gera 3 opções de thumbnails (A/B/C) sobrepondo o texto com Pillow."""
    output_dir = job_thumbnail_path(job_id)
    os.makedirs(output_dir, exist_ok=True)
    
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    openrouter_model = os.getenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/flux-1-schnell")
    hf_token = os.getenv("HF_TOKEN", "").strip() or None
    hf_model = os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")

    concepts = []
    if isinstance(variant_prompts, dict):
        concepts = variant_prompts.get("variants", [])
    elif isinstance(variant_prompts, list):
        concepts = variant_prompts

    styles = (
        "cinematic close-up, dramatic rim lighting",
        "bold editorial composition, visual metaphor",
        "documentary photography, strong emotional focal point",
    )
    
    results = []
    for index, letter in enumerate("ABC"):
        concept = concepts[index] if index < len(concepts) and isinstance(concepts[index], dict) else {}
        prompt = concept.get("prompt")
        overlay_text = concept.get("text") or title

        if not prompt:
            prompt = (
                f"YouTube thumbnail background about {primary_keyword}. {thumbnail_brief}. "
                f"{styles[index]}, high contrast, 16:9, subject on the right, empty dark area on top, "
                "no text, no words, no letters, no watermark"
            )

        output_path = str(output_dir / f"thumbnail_{letter}.jpg")
        image_bytes = None

        if openrouter_key:
            image_bytes = _fetch_openrouter_image(prompt=prompt, model=openrouter_model, api_key=openrouter_key)
            if image_bytes:
                logger.info("[Designer] Imagem gerada via OpenRouter API para variante %s", letter)

        if not image_bytes:
            image_bytes = _fetch_hf_image(prompt=prompt, model=hf_model, token=hf_token)
            if image_bytes:
                logger.info("[Designer] Imagem gerada via Hugging Face API para variante %s", letter)
        
        if image_bytes:
            _overlay_title(image_bytes, overlay_text, output_path, dominant_color)
        else:
            logger.warning("[Designer] Nenhuma API de imagem retornou dados para variante %s, ativando fallback local com gradiente", letter)
            _gradient_fallback(overlay_text, output_path, dominant_color)

        results.append({
            "variant": letter,
            "path": output_path,
            "prompt_used": prompt,
            "text_used": overlay_text,
            "is_active": letter == "A",
        })

    return results


def orchestrate_thumbnail_generation(
    job_id: UUID | str,
    title: str,
    language: str = "pt-BR",
    niche: str = "General",
    video_file_path: str | None = None,
    is_short: bool = False,
    **kwargs,
) -> dict:
    """Orquestração direta de thumbnails (A/B/C) com suporte a Shorts e parâmetros flexíveis."""
    results = generate_thumbnail_variants(
        job_id=job_id,
        title=title,
        thumbnail_brief=f"Niche: {niche}, Language: {language}",
        dominant_color="#FFD700",
        primary_keyword=niche,
    )
    selected = results[0] if results else {}
    return {"job_id": str(job_id), "thumbnails": results, "selected_thumbnail": selected}

