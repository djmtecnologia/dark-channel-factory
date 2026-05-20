import os
import requests
from shared.logger import get_logger

logger = get_logger(__name__)

# Configurações ElevenLabs (Multilingual v2)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
# Se vazio, usa a voz padrão (ex: Marcus/Adam). Se o usuário clonar o Cid Moreira, ele coloca o Voice ID aqui.
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJcg") # Exemplo: Adam

def generate_audio_elevenlabs(text: str, output_path: str, voice_id: str = None) -> bool:
    """
    Gera narração usando ElevenLabs (Suporta Vozes Clonadas e Multilíngue).
    """
    api_key = ELEVENLABS_API_KEY
    if not api_key:
        logger.error("❌ ELEVENLABS_API_KEY não configurada no .env!")
        return False

    vid = voice_id or ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }

    # Usando o modelo multilingual v2 para suportar PT, EN, FR, ES, DE com a mesma voz clonada
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True
        }
    }

    try:
        logger.info(f"🎙️ [ElevenLabs] Solicitando geração de áudio (Voice ID: {vid})...")
        response = requests.post(url, json=data, headers=headers, timeout=120)
        
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
            logger.info("✅ [ElevenLabs] Áudio gerado com sucesso!")
            return True
        else:
            logger.error(f"❌ [ElevenLabs] Erro na API: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ [ElevenLabs] Falha de conexão: {e}")
        return False
