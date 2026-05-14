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

import time

from shared.llm_client import get_llm_client, get_completion
from shared.logger import get_logger
from shared.storage import job_thumbnail_path
from agents.designer.prompts import THUMBNAIL_SYSTEM_PROMPT, THUMBNAIL_ORCHESTRATOR_USER_PROMPT

load_dotenv()
logger = get_logger(__name__)

REGION_CONFIG = {
    "en-US": {
        "style": "clean, bold, high contrast, minimal text",
        "text_style": "short aggressive hooks"
    },
    "en-GB": {
        "style": "slightly more editorial, less exaggerated",
        "text_style": "intrigue + subtle curiosity"
    },
    "de-DE": {
        "style": "structured, clear, informative",
        "text_style": "direct, factual impact"
    },
    "fr-FR": {
        "style": "aesthetic, cinematic, refined contrast",
        "text_style": "emotional but elegant"
    },
    "es-ES": {
        "style": "high emotion, expressive, vibrant",
        "text_style": "dramatic, curiosity-driven"
    }
}

def orchestrate_thumbnail_generation(
    job_id: UUID,
    title: str,
    language: str = "en-US",
    niche: str = "AI Automation",
    video_file_path: str | None = None,
    is_short: bool = False
) -> dict:
    """
    Main Orchestrator for Thumbnail Generation.
    Follows the 11-step specification: Multi-API -> Regional Style -> Fallback.
    """
    thumb_dir = job_thumbnail_path(job_id)
    region = language.split("-")[-1] if "-" in language else "US"
    
    logger.info(f"🎨 [Orchestrator] Starting for {language} in {region}")

    # 1. GERAÇÃO DE CONCEITOS VIA LLM
    prompt = THUMBNAIL_ORCHESTRATOR_USER_PROMPT.format(
        title=title,
        language=language,
        niche=niche,
        emotion_target="Surprise/Curiosity",
        region_code=region
    )
    response_json = get_completion(prompt, THUMBNAIL_SYSTEM_PROMPT, json_mode=True)
    import json
    try:
        data = json.loads(response_json)
    except Exception as e:
        logger.error(f"Failed to generate thumbnail concepts: {e}")
        logger.error(f"Raw Response Snippet: {response_json[:100]}...")
        data = {"thumbnails": []}

    results = []
    fallback_needed = True

    # 2. TENTATIVA DE GERAÇÃO VIA APIs (CASCAATA)
    # Atualmente suportamos apenas DALL-E 3 no llm_client, mas a estrutura está pronta
    for i, thumb_data in enumerate(data.get("thumbnails", [])[:3]):
        variant_letter = chr(65 + i) # A, B, C
        output_path = str(thumb_dir / f"thumbnail_{variant_letter}.png")
        
        success = False
        api_used = "DALL-E 3" 
        
        hardcoded_prefix = (
            "NEUROMARKETING STRICT RULES FOR YOUTUBE THUMBNAIL:\n"
            "1. HIGH DOPAMINE VISUALS: Use extreme color contrast (Neon vs Black). Instant 0.2s attention grab.\n"
            "2. THE 'PATTERN INTERRUPT': Break reality to trigger the amygdala (distorted familiar items, glowing red circles, giant arrows).\n"
            "3. FACIAL PSYCHOLOGY: Exaggerated micro-expressions (extreme shock, fear) with direct eye contact to the viewer.\n"
            "4. CURIOSITY GAP: Show 'Before' or 'Result', but HIDE the method. Guide the eye with a red circle or arrow.\n"
            "5. MASSIVE BOLD TEXT: Include exactly 1 or 2 massive, legible, glowing words in a bold Sans-Serif font.\n\n"
            "IMAGE CONTENT: "
        )
        final_prompt = hardcoded_prefix + thumb_data.get("prompt", "")
        
        llm = get_llm_client()
        image_bytes = None

        # 1st Attempt: DALL-E 3
        if llm._openai_client:
            try:
                # Remove request for text from DALL-E prompt
                dalle_prompt = final_prompt.replace("5. MASSIVE BOLD TEXT: Include exactly 1 or 2 massive, legible, glowing words in a bold Sans-Serif font.", "5. NO TEXT: Do not include any text, letters, or words in the image.")
                image_bytes = llm.generate_image(dalle_prompt)
            except Exception as e:
                logger.warning(f"DALL-E 3 failed: {e}")
        
        # 2nd Attempt: Pollinations.ai Fallback with Smart Retries
        if not image_bytes:
            api_used = "Pollinations.ai"
            logger.info("Tentando fallback via Pollinations.ai com 2 tentativas...")
            
            for attempt in range(2):
                try:
                    import urllib.parse
                    import requests
                    
                    raw_prompt = thumb_data.get("prompt", "")
                    hook_text = thumb_data.get("text", "")
                    
                    # Removemos qualquer pedido de texto para a IA para evitar gibberish (ex: FIIRED FnED)
                    if attempt == 1:
                        clean_prompt = f"YouTube thumbnail background, {title}, high contrast, no text, no letters, high quality"
                    else:
                        clean_prompt = f"A dramatic YouTube thumbnail background. Psychology triggers: extreme color contrast, shock facial expression looking at viewer, curiosity gap with red circle. NO TEXT, NO LETTERS. Visuals: " + raw_prompt.replace('\n', ' ').strip()
                    
                    safe_prompt = urllib.parse.quote(clean_prompt[:800])
                    poll_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1280&height=720&nologo=true&enhance=true&seed={int(time.time()) + attempt}"
                    
                    response = requests.get(poll_url, timeout=90)
                    if response.status_code == 200 and len(response.content) > 10000:
                        image_bytes = response.content
                        break
                    else:
                        logger.warning(f"Pollinations.ai falhou (Tentativa {attempt+1})")
                except Exception as e:
                    logger.warning(f"Erro na tentativa {attempt+1} do Pollinations: {e}")
                time.sleep(2)

        # Se obteve a imagem de qualquer uma das APIs, salva e aplica o texto perfeitamente via PIL
        if image_bytes:
            with open(output_path, "wb") as f:
                f.write(image_bytes)
                
            # INJEÇÃO NATIVA DE TEXTO (Modelo Elite Crisis: Top-Aligned + Red Badge)
            try:
                from PIL import Image, ImageDraw, ImageFont
                
                target_w, target_h = (720, 1280) if is_short else (1280, 720)
                img = Image.open(output_path).convert("RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
                
                # 1. Gradiente Superior para Legibilidade
                overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
                draw_overlay = ImageDraw.Draw(overlay)
                for y in range(int(target_h * 0.35)):
                    alpha = int(220 * (1 - y / (target_h * 0.35)))
                    draw_overlay.line([(0, y), (target_w, y)], fill=(0, 0, 0, alpha))
                img = Image.alpha_composite(img, overlay)
                
                draw = ImageDraw.Draw(img)
                hook_text = thumb_data.get("text", title[:15]).upper()
                
                # 2. Renderização do Texto Principal (Topo)
                font_size = 130 if not is_short else 90
                font = None
                try:
                    while font_size > 40:
                        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
                        left, top, right, bottom = draw.textbbox((0, 0), hook_text, font=font)
                        if (right - left) <= target_w - 80: break
                        font_size -= 5
                except: font = ImageFont.load_default()
                
                # Desenha Sombra Pesada para profundidade
                draw.text((target_w//2+4, 100+4), hook_text, font=font, fill="black", anchor="mt")
                # Desenha Texto Principal (Neon Yellow)
                draw.text((target_w//2, 100), hook_text, font=font, fill="#FFFF00", anchor="mt", stroke_width=4, stroke_fill="black")
                
                # 3. Injeção do Badge Vermelho "¡URGENTE!" ou "WATCH NOW"
                try:
                    badge_text = "¡URGENTE!" if "es-" in language else ("URGENT!" if "en-" in language else "WATCH NOW")
                    badge_font_size = int(font_size * 0.35)
                    badge_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", badge_font_size)
                    bl, bt, br, bb = draw.textbbox((0, 0), badge_text, font=badge_font)
                    bw, bh = br - bl + 40, bb - bt + 20
                    bx, by = target_w//2 - bw//2, 100 + (bottom - top) + 20
                    
                    # Desenha Retângulo Vermelho (Badge)
                    draw.rectangle([bx, by, bx+bw, by+bh], fill="#FF0000")
                    # Desenha Texto do Badge (Branco)
                    draw.text((bx + bw//2, by + bh//2), badge_text, font=badge_font, fill="white", anchor="mm")
                except: pass

                img.convert("RGB").save(output_path, "JPEG", quality=95)
            except Exception as e:
                logger.error(f"Erro ao desenhar texto na thumbnail: {e}")

            success = True
            fallback_needed = False
        else:
            api_used = "failed"
        
        results.append({
            "variant": variant_letter,
            "text": thumb_data.get("text"),
            "prompt": thumb_data.get("prompt"),
            "score": thumb_data.get("score"),
            "path": output_path if success else None,
            "api_used": api_used if success else "failed"
        })

    # 3. FALLBACK DE FRAME DE VÍDEO
    if fallback_needed and video_file_path and os.path.exists(video_file_path):
        logger.warning("🚨 [Orchestrator] All Image APIs failed. Using Video Frame Fallback.")
        fallback_path = str(thumb_dir / "thumbnail_fallback.jpg")
        fallback_text = data["thumbnails"][0]["text"] if data.get("thumbnails") else title[:20].upper()
        
        success = _generate_video_frame_fallback(
            video_path=video_file_path,
            output_path=fallback_path,
            text=fallback_text
        )
        
        if success:
            results.append({
                "variant": "FALLBACK_FRAME",
                "text": fallback_text,
                "path": fallback_path,
                "api_used": "Video Frame + FFmpeg",
                "score": 75
            })
            fallback_needed = False

    # 4. FALLBACK FINAL: GRADIENTE DESIGN (Garantia de que nunca fique sem thumb)
    if fallback_needed:
        logger.warning("🚨 [Orchestrator] Using Final Gradient Fallback (Zero Assets).")
        fallback_path = str(thumb_dir / "thumbnail_final_gradient.jpg")
        fallback_text = data["thumbnails"][0]["text"] if (data.get("thumbnails") and data["thumbnails"][0].get("text")) else title[:25].upper()
        
        success = _generate_gradient_fallback(
            output_path=fallback_path,
            text=fallback_text,
            is_short=is_short
        )
        
        if success:
            results.append({
                "variant": "FALLBACK_GRADIENT",
                "text": fallback_text,
                "path": fallback_path,
                "api_used": "Pillow Design Engine",
                "score": 65
            })
            fallback_needed = False

    # Selecionar a melhor (maior score)
    valid_results = [r for r in results if r["path"]]
    selected = max(valid_results, key=lambda x: x["score"]) if valid_results else {}

    return {
        "region": region,
        "language": language,
        "thumbnails": results,
        "selected_thumbnail": selected,
        "fallback_used": fallback_needed,
        "notes": "Generated via Autonomous Orchestrator"
    }

def _generate_video_frame_fallback(video_path: str, output_path: str, text: str) -> bool:
    """
    Implementação do Passo 5: Fallback Final via Frame de Vídeo.
    Agora mais seguro, usando PIL para renderizar texto (evita erros do FFmpeg no Windows).
    """
    try:
        import subprocess
        from PIL import Image, ImageDraw, ImageFont
        
        # 1. Extrai frame do vídeo (scale + correção de cor) via FFmpeg
        # Avança 30 segundos para garantir que pega uma cena real (evita fundos pretos do início)
        vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.2:saturation=1.4"
        cmd = f'ffmpeg -ss 00:00:30 -i "{video_path}" -vframes 1 -vf "{vf}" -q:v 2 "{output_path}" -y'
        
        res = subprocess.run(cmd, shell=True, capture_output=True)
        if res.returncode != 0:
            logger.error(f"FFmpeg extract failed: {res.stderr.decode('utf-8', errors='ignore')}")
            return False

        # 2. Adiciona os elementos de design (tarja e texto) via Pillow
        if os.path.exists(output_path):
            img = Image.open(output_path).convert("RGBA")
            
            # Tarja Preta Transparente
            overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
            draw_overlay = ImageDraw.Draw(overlay)
            draw_overlay.rectangle([(0, 360-100), (1280, 360+100)], fill=(0, 0, 0, 170)) # 170/255 opacity
            img = Image.alpha_composite(img, overlay)
            
            draw = ImageDraw.Draw(img)
            # Dynamic Font Sizing no Fallback
            font_size = 90
            font = None
            try:
                while font_size > 30:
                    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
                    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
                    text_width = right - left
                    if text_width <= 1200:
                        break
                    font_size -= 5
            except Exception:
                font = ImageFont.load_default()
                
            # Texto centralizado
            draw.text((1280//2, 720//2), text, font=font, fill="yellow", anchor="mm", stroke_width=4, stroke_fill="black")
            
            img.convert("RGB").save(output_path, "JPEG", quality=90)
            return True
            
        return False
    except Exception as e:
        logger.error(f"Video frame fallback failed: {e}")
        return False

def _generate_gradient_fallback(output_path: str, text: str, is_short: bool = False) -> bool:
    """
    Gera uma thumbnail de alta qualidade baseada em gradientes e tipografia.
    Garante que o vídeo nunca fique sem thumb mesmo sem internet ou APIs.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import random
        
        w, h = (720, 1280) if is_short else (1280, 720)
        
        # 1. Cria Fundo Gradiente Dinâmico
        color_start = (random.randint(10, 40), random.randint(10, 40), random.randint(30, 80))
        color_end = (random.randint(80, 120), 0, random.randint(150, 200))
        
        img = Image.new('RGB', (w, h), color=color_start)
        draw = ImageDraw.Draw(img)
        
        for i in range(h):
            r = int(color_start[0] + (color_end[0] - color_start[0]) * (i / h))
            g = int(color_start[1] + (color_end[1] - color_start[1]) * (i / h))
            b = int(color_start[2] + (color_end[2] - color_start[2]) * (i / h))
            draw.line([(0, i), (w, i)], fill=(r, g, b))
            
        # 2. Adiciona "Partículas" de Bokeh para profundidade
        for _ in range(15):
            x, y = random.randint(0, w), random.randint(0, h)
            size = random.randint(50, 300)
            draw.ellipse([x-size, y-size, x+size, y+size], fill=(255, 255, 255, 20))
            
        img = img.filter(ImageFilter.GaussianBlur(radius=20))
        draw = ImageDraw.Draw(img)
        
        # 3. Tipografia de Alto Impacto
        font_size = 120 if not is_short else 90
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
        except:
            font = ImageFont.load_default()
            
        # Quebra o texto se for muito longo
        words = text.split()
        if len(words) > 3:
            text_lines = [" ".join(words[:3]), " ".join(words[3:6])]
            text = "\n".join(text_lines)
            
        # Desenha Sombra
        draw.multiline_text((w//2+5, h//2+5), text, font=font, fill="black", anchor="mm", align="center")
        # Desenha Texto Principal
        draw.multiline_text((w//2, h//2), text, font=font, fill="yellow", anchor="mm", align="center", stroke_width=4, stroke_fill="red")
        
        img.save(output_path, "JPEG", quality=95)
        return True
    except Exception as e:
        logger.error(f"Gradient fallback failed: {e}")
        return False
