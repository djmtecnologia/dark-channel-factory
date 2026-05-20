"""
Chatterbox TTS — Narração Cinematográfica para Dark Channel Factory
===================================================================
Motor de síntese de voz FREE & Open-Source (MIT License) que rivaliza o ElevenLabs.

Características:
  - Parâmetro `exaggeration` (0.0-1.5): controla intensidade dramática
  - Parâmetro `cfg_weight` (0.1-1.0): controla ritmo e cadência
  - Zero-shot voice cloning: apenas 5-10s de áudio de referência
  - Suporta EN, ES, FR, DE via pós-processamento de texto
  - Roda em CPU (lento) e GPU CUDA (rápido)

Instalação:
  pip install chatterbox-tts torchaudio soundfile

Documentação: https://github.com/resemble-ai/chatterbox
"""
from __future__ import annotations

import os
import re
import time
import tempfile
import subprocess
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# VOICE PROFILES — configurações por tipo de conteúdo
# ─────────────────────────────────────────────────────────────────

VOICE_PROFILES = {
    # 🎬 Dark / Mistério / True Crime — voz grave com drama máximo
    "dark_dramatic": {
        "exaggeration": 0.85,
        "cfg_weight": 0.45,
        "description": "Narração cinematográfica sombria, pausas dramáticas, tom grave",
    },
    # 🕵️ Documental / Investigação — equilibrado, sério, com urgência
    "dark_documentary": {
        "exaggeration": 0.65,
        "cfg_weight": 0.55,
        "description": "Tom documentário, autoritário, progressão de tensão",
    },
    # 💰 Finanças / Tech — direto, urgente, assertivo
    "finance_tech": {
        "exaggeration": 0.50,
        "cfg_weight": 0.70,
        "description": "Narração financeira profissional, ritmo rápido mas claro",
    },
    # 🌙 ASMR Dark / Sussurro — íntimo, misterioso
    "dark_whisper": {
        "exaggeration": 0.40,
        "cfg_weight": 0.35,
        "description": "Voz sussurrada cinematográfica, íntima e perturbadora",
    },
    # 📰 Neutro / Audiobook — natural, fluido
    "neutral": {
        "exaggeration": 0.35,
        "cfg_weight": 0.75,
        "description": "Narração natural e fluida, sem exagero emocional",
    },
}

# ─────────────────────────────────────────────────────────────────
# SSML-LIKE TEXT PREPROCESSOR (adiciona pausas dramáticas ao texto)
# ─────────────────────────────────────────────────────────────────

# Marcações especiais que você pode usar no roteiro:
# [PAUSA] → pausa curta (0.5s)
# [PAUSA_LONGA] → pausa dramática (1.5s)
# [SUSPIRO] → respira antes de continuar
# [ACELERAÇÃO] → próximo trecho mais rápido
# [DESTAQUE:texto] → enfatiza a frase

_PAUSE_PATTERNS = [
    # Pontos de pausa naturais — injeta reticências para o TTS pausar
    (r'([.!?])\s+([A-ZÁÉÍÓÚÀÂÊÔÃÕ])', r'\1 ... \2'),   # Pausa entre frases
    (r'([,;])\s+([a-záéíóúàâêôãõ])', r'\1 \2'),          # Vírgulas: manter natural
    # Marcações explícitas do roteiro
    (r'\[PAUSA\]', ' ... '),
    (r'\[PAUSA_LONGA\]', ' ...... '),
    (r'\[SUSPIRO\]', ' ... '),
    (r'\[ACELERAÇÃO\]', ''),
    (r'\[DESTAQUE:([^\]]+)\]', r'\1'),
    # Números por extenso melhora pronúncia
    (r'\$(\d+)B', r'\1 billion dollars'),
    (r'\$(\d+)M', r'\1 million dollars'),
    (r'(\d+)%', r'\1 percent'),
    # Remove markdown
    (r'\*\*([^*]+)\*\*', r'\1'),
    (r'\*([^*]+)\*', r'\1'),
    (r'`([^`]+)`', r'\1'),
    (r'#+\s+', ''),
]

def preprocess_text_for_tts(text: str, language: str = "en-US") -> str:
    """
    Limpa e enriquece o texto para síntese dramática.
    Remove markdown, adiciona pausas naturais, expande abreviações.
    """
    for pattern, replacement in _PAUSE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    
    # Remove múltiplos espaços/linhas
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r' {2,}', ' ', text)
    text = text.strip()
    
    return text


def _split_into_chunks(text: str, max_chars: int = 500) -> list[str]:
    """
    Chatterbox funciona melhor com chunks de até 500 chars.
    Divide respeitando limites de sentença para evitar cortes abruptos.
    """
    if len(text) <= max_chars:
        return [text]
    
    # Divide por pontuação forte
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = ""
    
    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current = current + " " + sent if current else sent
        else:
            if current:
                chunks.append(current.strip())
            current = sent
    
    if current:
        chunks.append(current.strip())
    
    return chunks if chunks else [text[:max_chars]]


# ─────────────────────────────────────────────────────────────────
# CORE TTS ENGINE
# ─────────────────────────────────────────────────────────────────

_model_cache = {}  # Singleton cache to avoid reloading model each call

def _load_chatterbox():
    """Lazy-load the Chatterbox model. Cached after first call."""
    if "model" in _model_cache:
        return _model_cache["model"]
    
    try:
        from chatterbox.tts import ChatterboxTTS
        import torch
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"🎙️ [CHATTERBOX] Carregando modelo... (device: {device})")
        print("   ℹ️  Primeira carga pode demorar 30-60s (download ~1GB)")
        
        model = ChatterboxTTS.from_pretrained(device=device)
        _model_cache["model"] = model
        
        print(f"✅ [CHATTERBOX] Modelo carregado! Sample rate: {model.sr}Hz")
        return model
        
    except ImportError:
        raise ImportError(
            "Chatterbox TTS não instalado. Execute:\n"
            "pip install chatterbox-tts torchaudio soundfile"
        )


def generate_chatterbox_audio(
    text: str,
    output_path: str,
    voice_profile: str = "dark_documentary",
    reference_audio: Optional[str] = None,
    language: str = "en-US",
) -> bool:
    """
    Gera narração cinematográfica usando Chatterbox TTS.
    
    Args:
        text: Texto do roteiro (pode incluir marcações [PAUSA], [DESTAQUE:...])
        output_path: Caminho de saída do arquivo de áudio (.mp3 ou .wav)
        voice_profile: Perfil de voz (ver VOICE_PROFILES acima)
        reference_audio: Caminho para áudio de referência (voice cloning, 5-30s .wav)
        language: Código de idioma (afeta pré-processamento)
    
    Returns:
        True se gerado com sucesso, False se falhou.
    """
    import sys
    try:
        import torchaudio as ta
        import soundfile as sf
        import numpy as np
    except ImportError:
        print("❌ [CHATTERBOX] Dependências ausentes: pip install torchaudio soundfile")
        return False
    
    profile = VOICE_PROFILES.get(voice_profile, VOICE_PROFILES["dark_documentary"])
    exaggeration = profile["exaggeration"]
    cfg_weight = profile["cfg_weight"]
    
    # Pré-processa o texto
    clean_text = preprocess_text_for_tts(text, language)
    
    if not clean_text.strip():
        print("⚠️ [CHATTERBOX] Texto vazio após pré-processamento.")
        return False
    
    print(f"🎙️ [CHATTERBOX] Gerando narração '{voice_profile}' | exaggeration={exaggeration} | cfg={cfg_weight}")
    print(f"   📝 Texto: {clean_text[:100]}...")
    
    try:
        model = _load_chatterbox()
        chunks = _split_into_chunks(clean_text, max_chars=450)
        
        print(f"   📦 Dividido em {len(chunks)} chunk(s)")
        
        all_audio = []
        silence_samples = int(model.sr * 0.35)  # 350ms de silêncio entre chunks
        silence_chunk = [0.0] * silence_samples  # type: ignore
        
        for idx, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            
            print(f"   🔄 Chunk {idx+1}/{len(chunks)}: {chunk[:60]}...")
            
            kwargs = {
                "text": chunk,
                "exaggeration": exaggeration,
                "cfg_weight": cfg_weight,
            }
            
            # Voice cloning via audio de referência
            if reference_audio and os.path.exists(reference_audio):
                kwargs["audio_prompt_path"] = reference_audio
                print(f"   🎤 Clonando voz de: {os.path.basename(reference_audio)}")
            
            wav = model.generate(**kwargs)
            
            # wav pode ser tensor ou ndarray
            if hasattr(wav, 'numpy'):
                audio_np = wav.squeeze().numpy()
            elif hasattr(wav, 'cpu'):
                audio_np = wav.cpu().squeeze().numpy()
            else:
                audio_np = wav
            
            all_audio.append(audio_np)
            if idx < len(chunks) - 1:
                all_audio.append(silence_chunk)  # type: ignore
        
        if not all_audio:
            print("❌ [CHATTERBOX] Nenhum áudio gerado.")
            return False
        
        # Concatena todos os chunks
        final_audio = np.concatenate([
            np.array(a, dtype=np.float32) for a in all_audio
        ])
        
        # Salva como WAV temporário primeiro
        temp_wav = output_path.rsplit('.', 1)[0] + "_chatterbox_tmp.wav"
        sf.write(temp_wav, final_audio, model.sr)
        
        # Converte para MP3 usando FFmpeg (preserva compatibilidade com o pipeline)
        ffmpeg_exe = "ffmpeg"
        for candidate in [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\ffmpeg.exe",
        ]:
            if os.path.exists(candidate):
                ffmpeg_exe = candidate
                break
        
        # Normaliza áudio EBU R128 e converte para MP3
        convert_cmd = [
            ffmpeg_exe, "-y", "-i", temp_wav,
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "libmp3lame", "-q:a", "2",
            output_path
        ]
        res = subprocess.run(convert_cmd, capture_output=True, text=True)
        
        try:
            os.remove(temp_wav)
        except:
            pass
        
        if res.returncode == 0 and os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) // 1024
            print(f"✅ [CHATTERBOX] Narração gerada! {size_kb}KB → {output_path}")
            return True
        else:
            print(f"❌ [CHATTERBOX] FFmpeg falhou: {res.stderr[:300]}")
            # Fallback: salva WAV direto se MP3 falhar
            if os.path.exists(temp_wav):
                import shutil
                shutil.move(temp_wav, output_path.replace('.mp3', '.wav'))
            return False
    
    except Exception as e:
        print(f"❌ [CHATTERBOX] Erro na geração: {e}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────────────────────────
# VOICE PROFILE AUTO-SELECTOR
# ─────────────────────────────────────────────────────────────────

def select_voice_profile(job_data: dict) -> str:
    """
    Seleciona automaticamente o perfil de voz com base nos metadados do job.
    Prioridade: job_data.voice_profile > niche detection > default
    """
    # Override explícito no job
    if job_data.get("voice_profile"):
        return job_data["voice_profile"]
    
    # Detecção por nicho/tópico
    topic = str(job_data.get("topic", "") or "").lower()
    niche = str(job_data.get("niche", "") or "").lower()
    combined = topic + " " + niche
    
    dark_keywords = [
        "mistério", "mystery", "crime", "caso", "desapareci", "assassin",
        "proibid", "forbidden", "dark", "horror", "assombra", "secreto", "secret",
        "encobri", "escondido", "arquivo", "arquivo proibido", "found footage",
        "perturbador", "bizarre", "disturbing", "conspiração", "conspiracy",
        "experimento", "experiment", "deep web", "policia", "investigation",
        "gravação", "recording", "fita", "tape", "câmera", "camera",
        "mystère", "interdit", "Verbrechen", "Verbot", "Geheimnis"
    ]
    
    finance_keywords = [
        "trading", "stock", "market", "invest", "fintech", "crypto",
        "bitcoin", "portfolio", "hedge", "fund", "financ", "economia",
        "börse", "marché", "bourse", "mercado"
    ]
    
    if any(kw in combined for kw in dark_keywords):
        return "dark_dramatic"
    elif any(kw in combined for kw in finance_keywords):
        return "finance_tech"
    else:
        return "dark_documentary"


# ─────────────────────────────────────────────────────────────────
# INSTALLATION CHECKER
# ─────────────────────────────────────────────────────────────────

def check_chatterbox_available() -> bool:
    """Returns True if chatterbox-tts is installed and ready."""
    try:
        import chatterbox  # noqa: F401
        import torchaudio  # noqa: F401
        import soundfile   # noqa: F401
        return True
    except ImportError:
        return False


def print_install_instructions():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║           CHATTERBOX TTS — INSTRUÇÕES DE INSTALAÇÃO              ║
╠══════════════════════════════════════════════════════════════════╣
║  pip install chatterbox-tts torchaudio soundfile                 ║
║                                                                  ║
║  Para uso com GPU (NVIDIA):                                      ║
║  pip install torch --index-url https://download.pytorch.org/     ║
║       whl/cu121                                                  ║
║                                                                  ║
║  Licença: MIT — Uso comercial permitido                          ║
║  Repo: https://github.com/resemble-ai/chatterbox                 ║
╚══════════════════════════════════════════════════════════════════╝
""")
