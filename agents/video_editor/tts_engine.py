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
        "male_professional": "en-US-ChristopherNeural",
        "female_energetic": "en-US-JennyNeural",
        "male_energetic": "en-US-EricNeural",
        "default": "en-US-ChristopherNeural",
    },
    "pt-BR": {
        "female_professional": "pt-BR-FranciscaNeural",
        "male_professional": "pt-BR-ValerioNeural",       # Voz extremamente grave e jornalística (estilo documentário/mistério)
        "default": "pt-BR-ValerioNeural",
    },
    "es-ES": {
        "female_professional": "es-ES-ElviraNeural",
        "male_professional": "es-ES-AlvaroNeural",
        "default": "es-ES-AlvaroNeural",
    },
    "fr-FR": {
        "female_professional": "fr-FR-DeniseNeural",
        "male_professional": "fr-FR-HenriNeural",
        "default": "fr-FR-HenriNeural",
    },
    "de-DE": {
        "female_professional": "de-DE-KatjaNeural",
        "male_professional": "de-DE-KillianNeural",
        "default": "de-DE-KillianNeural",
    },
    "it-IT": {
        "female_professional": "it-IT-ElsaNeural",
        "male_professional": "it-IT-DiegoNeural",
        "default": "it-IT-DiegoNeural",
    },
}

def get_voice_for_language(language: str, style: str = "male_professional") -> str:
    """
    Retorna a voz neural ideal baseada dinamicamente no idioma do roteiro.
    Suporta códigos de idioma no formato 'll-CC' (ex: 'en-US', 'de-DE', 'es-ES'), 
    prefixos genéricos (ex: 'en', 'de', 'es') e variantes de região.
    """
    if not language:
        return RECOMMENDED_VOICES["en-US"]["default"]

    # Mapeamento de variações de idioma para a chave base do dicionário
    lang_map = {
        "en": "en-US", "en-us": "en-US", "en-gb": "en-US", "en-au": "en-US",
        "pt": "pt-BR", "pt-br": "pt-BR", "pt-pt": "pt-BR",
        "es": "es-ES", "es-es": "es-ES", "es-mx": "es-ES", "es-co": "es-ES",
        "fr": "fr-FR", "fr-fr": "fr-FR", "fr-ca": "fr-FR",
        "de": "de-DE", "de-de": "de-DE", "de-at": "de-DE", "de-ch": "de-DE",
        "it": "it-IT", "it-it": "it-IT",
    }

    norm_lang = language.strip().lower()
    
    # 1. Checa correspondência exata ou normalizada pelo lang_map
    target_key = lang_map.get(norm_lang)
    
    # 2. Se não encontrou, tenta pelo prefixo de 2 letras (ex: 'en-NZ' -> 'en')
    if not target_key and "-" in norm_lang:
        prefix = norm_lang.split("-")[0]
        target_key = lang_map.get(prefix)
    
    # 3. Tenta encontrar chave insensível a maiúsculas/minúsculas no RECOMMENDED_VOICES
    if not target_key:
        for k in RECOMMENDED_VOICES:
            if k.lower() == norm_lang:
                target_key = k
                break

    # Caso ainda não encontre, utiliza 'en-US' como fallback
    if not target_key or target_key not in RECOMMENDED_VOICES:
        target_key = "en-US"

    voices = RECOMMENDED_VOICES[target_key]
    return voices.get(style, voices.get("default", "en-US-ChristopherNeural"))


def _build_ssml_for_edge(
    text: str,
    voice: str,
    rate: str = "+0%",
    volume: str = "+10%",
    pitch: str = "-5Hz",
    lang: str = "en-US",
) -> str:
    """
    Súper-carrega o edge-tts convertendo marcações dramáticas e pontuações em SSML.
    Cria pausas realistas, ênfases cinematográficas e evita leitura monótona "robótica".
    """
    import html
    import re

    # 1. Escapa entidades XML básicas para evitar crash no parser do Azure
    escaped_text = html.escape(text)

    # 2. Converte tags do roteiro em pausas e entonações dramáticas
    # [PAUSA_LONGA] -> pausa dramática de suspense
    escaped_text = re.sub(r'\[PAUSA_LONGA\]', '<break time="1200ms"/>', escaped_text, flags=re.IGNORECASE)
    # [PAUSA] -> pausa de impacto ou transição
    escaped_text = re.sub(r'\[PAUSA\]', '<break time="500ms"/>', escaped_text, flags=re.IGNORECASE)
    # [SUSPIRO] -> micro silêncio para simular respiração natural
    escaped_text = re.sub(r'\[SUSPIRO\]', '<break time="700ms"/>', escaped_text, flags=re.IGNORECASE)

    # [DESTAQUE:texto] -> prosódia lenta, mais alta e tom grave (tom conspiratório/revelador)
    escaped_text = re.sub(
        r'\[DESTAQUE:([^\]]+)\]',
        r'<prosody pitch="-4Hz" rate="-12%" volume="+15%">\1</prosody>',
        escaped_text,
        flags=re.IGNORECASE
    )

    # [ACELERAÇÃO:texto] ou [ACELERA:texto] -> prosódia acelerada para urgência/ação
    escaped_text = re.sub(
        r'\[ACELERA(?:ÇÃO)?:([^\]]+)\]',
        r'<prosody rate="+18%">\1</prosody>',
        escaped_text,
        flags=re.IGNORECASE
    )

    # 3. Adiciona micro-pausas inteligentes baseadas na pontuação natural (evita a cadência IA linear)
    # Período '.' seguido de espaço e maiúscula
    escaped_text = re.sub(r'\.\s+([A-ZÁÉÍÓÚÀÂÊÔÃÕ])', r'. <break time="450ms"/>\1', escaped_text)
    # Ponto de interrogação '?'
    escaped_text = re.sub(r'\?\s+', r'? <break time="550ms"/>', escaped_text)
    # Ponto de exclamação '!'
    escaped_text = re.sub(r'\!\s+', r'! <break time="550ms"/>', escaped_text)
    # Reticências '...'
    escaped_text = re.sub(r'\.\.\.\s*', r'... <break time="650ms"/>', escaped_text)
    # Vírgulas ','
    escaped_text = re.sub(r'\,\s+', r', <break time="180ms"/>', escaped_text)

    # 4. Envolve no speak/voice/prosody envelope
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">\n'
        f'  <voice name="{voice}">\n'
        f'    <prosody rate="{rate}" volume="{volume}" pitch="{pitch}">\n'
        f'      {escaped_text}\n'
        f'    </prosody>\n'
        f'  </voice>\n'
        f'</speak>'
    )
    return ssml


async def _generate_tts_async(
    text: str,
    voice: str,
    output_path: str,
    rate: str = "+0%",
    volume: str = "+10%",
    pitch: str = "-5Hz",
) -> str:
    """Gera áudio TTS de forma assíncrona. Suporta texto limpo ou SSML xml."""
    if text.strip().startswith("<speak"):
        communicate = edge_tts.Communicate(text=text)
    else:
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
    rate: str = "+0%",
    volume: str = "+10%",
) -> str:
    """
    Gera áudio para uma cena específica de acordo com o motor configurado.
    Suporta: ElevenLabs, Coqui XTTSv2 (Local Cloner), Resemble Chatterbox e Supercharged Edge-TTS.
    Retorna o caminho do arquivo .mp3 gerado.
    """
    audio_dir = job_audio_path(job_id)
    output_path = str(audio_dir / f"scene_{scene_id:03d}.mp3")

    engine = os.getenv("TTS_ENGINE", "edge").lower()
    logger.info(f"🎙️ Generating TTS for scene_{scene_id:03d} using engine: '{engine}'")

    # Mapeamento do idioma para local cloner
    lang_code = "pt-BR" if "pt-br" in voice.lower() else "en-US"
    if "-" in voice:
        parts = voice.split("-")
        if len(parts) >= 2:
            lang_code = f"{parts[0]}-{parts[1]}"

    # --- ENGINE 1: ELEVENLABS ---
    if engine == "elevenlabs":
        try:
            from agents.video_editor.elevenlabs_tts import generate_audio_elevenlabs
            voice_id = os.getenv("ELEVENLABS_VOICE_ID")
            success = generate_audio_elevenlabs(text, output_path, voice_id=voice_id)
            if success and os.path.exists(output_path):
                return output_path
            logger.warning("⚠️ ElevenLabs failed, falling back to supercharged Edge-TTS.")
        except Exception as e:
            logger.error(f"❌ ElevenLabs integration failed: {e}. Falling back to edge.")

    # --- ENGINE 2: COQUI XTTSv2 (Local Voice Cloner) ---
    elif engine == "xtts":
        try:
            from agents.video_editor.local_voice_cloner import generate_local_cloned_voice
            # Localiza arquivo de áudio de referência de clonagem (Cid Moreira)
            ref_path = os.getenv("XTTS_REF_AUDIO", "cid_moreira_reference.wav")
            if not os.path.exists(ref_path):
                for candidate in ["teste_cid_moreira.wav", "referencia_local_35b0a7a7.wav"]:
                    if os.path.exists(candidate):
                        ref_path = candidate
                        break
            
            if os.path.exists(ref_path):
                logger.info(f"👥 [XTTSv2] Cloning voice from reference audio: '{ref_path}'")
                success = generate_local_cloned_voice(text, lang_code, ref_path, output_path)
                if success and os.path.exists(output_path):
                    return output_path
            else:
                logger.error(f"❌ XTTS reference file not found! Place 'cid_moreira_reference.wav' in root.")
            logger.warning("⚠️ XTTS failed, falling back to supercharged Edge-TTS.")
        except Exception as e:
            logger.error(f"❌ XTTS local cloner failed: {e}. Falling back to edge.")

    # --- ENGINE 3: RESEMBLE CHATTERBOX (Local Dramatic TTS) ---
    elif engine == "chatterbox":
        try:
            from agents.video_editor.chatterbox_tts import generate_chatterbox_audio
            ref_path = os.getenv("XTTS_REF_AUDIO", "cid_moreira_reference.wav")
            if not os.path.exists(ref_path):
                for candidate in ["teste_cid_moreira.wav", "referencia_local_35b0a7a7.wav"]:
                    if os.path.exists(candidate):
                        ref_path = candidate
                        break
            profile = os.getenv("CHATTERBOX_PROFILE", "dark_dramatic")
            logger.info(f"🎭 [Chatterbox] Generating with profile '{profile}'...")
            success = generate_chatterbox_audio(
                text=text,
                output_path=output_path,
                voice_profile=profile,
                reference_audio=ref_path if os.path.exists(ref_path) else None,
                language=lang_code
            )
            if success and os.path.exists(output_path):
                return output_path
            logger.warning("⚠️ Chatterbox failed, falling back to supercharged Edge-TTS.")
        except Exception as e:
            logger.error(f"❌ Chatterbox engine failed: {e}. Falling back to edge.")

    # --- ENGINE 4 (FALLBACK / PADRÃO): SUPERCHARGED EDGE-TTS WITH SSML ---
    # Limpa texto removendo marcações pesadas mas mantendo formatação SSML
    pitch = os.getenv("TTS_PITCH", "-5Hz") # Tom grave conspiratório/suspense por padrão
    ssml = _build_ssml_for_edge(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        lang=lang_code
    )

    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        # Already in an async context: run in a thread pool
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _generate_tts_async(
                text=ssml, voice=voice, output_path=output_path, rate=rate, volume=volume, pitch=pitch
            ))
            future.result()
    else:
        asyncio.run(_generate_tts_async(
            text=ssml, voice=voice, output_path=output_path, rate=rate, volume=volume, pitch=pitch
        ))

    if os.path.exists(output_path):
        size_kb = Path(output_path).stat().st_size // 1024
        logger.debug(f"🎤 SSML Edge-TTS scene_{scene_id:03d} | voice={voice} | {size_kb}KB")
        return output_path
    else:
        raise RuntimeError(f"Failed to generate TTS audio for scene {scene_id}")


def generate_full_narration(
    scenes: list[dict],
    job_id: UUID,
    voice: str = "en-US-AriaNeural",
    rate: str = "+0%",
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
    """Remove caracteres que podem causar problemas no TTS sem quebrar SSML."""
    import re
    # Remove tags que não sejam de SSML ou customizadas
    # Remove markdown symbols (exceto chaves e colchetes das tags customizadas)
    text = re.sub(r"[*_`#>\(\)]", "", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # BUG FIX: Remove only actual emojis (Unicode 'So'/'Cs' categories), NOT accented letters
    text = re.sub(r"[\U00010000-\U0010FFFF]", "", text, flags=re.UNICODE)  # Surrogates/emoji
    text = re.sub(r"[\U0001F300-\U0001FAFF]", "", text, flags=re.UNICODE)  # Main emoji blocks
    # Colapsa espaços múltiplos
    text = " ".join(text.split())
    return text.strip()


async def list_available_voices(language: str = "en-US") -> list[dict]:
    """Lista vozes disponíveis para um idioma."""
    voices = await edge_tts.list_voices()
    return [v for v in voices if v.get("Locale", "").startswith(language)]
