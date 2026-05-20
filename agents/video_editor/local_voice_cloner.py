import os
import sys

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ["COQUI_TOS_AGREED"] = "1"
import subprocess
import torch

# Adiciona a raiz do projeto ao path para encontrar o módulo 'shared'
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.logger import get_logger

logger = get_logger(__name__)

# Configurações do XTTSv2 (Coqui TTS)
# O XTTSv2 é um modelo open-source poderoso que faz clonagem de voz (zero-shot) 
# e suporta múltiplos idiomas (pt, en, es, fr, de, etc.)
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app_data", "models", "tts")

def download_reference_audio(youtube_url: str, output_wav: str, start_time: str = "00:00:04", duration: str = "00:00:14") -> bool:
    """
    Usa yt-dlp e ffmpeg para baixar o vídeo do YouTube e extrair um trecho limpo
    de áudio para usar como referência de clonagem.
    """
    logger.info(f"📥 [Clonagem] Baixando áudio de referência: {youtube_url}")
    
    temp_audio = output_wav.replace(".wav", "_temp.m4a")
    
    yt_dlp_cmd = [
        "yt-dlp",
        "-f", "bestaudio[ext=m4a]",
        "-o", temp_audio,
        youtube_url
    ]
    
    try:
        subprocess.run(yt_dlp_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        logger.info("🎵 [Clonagem] Processando áudio para o padrão do modelo...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", temp_audio,
            "-ss", start_time, "-t", duration,
            "-ac", "1", "-ar", "22050",
            output_wav
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
            
        logger.info("✅ [Clonagem] Áudio de referência pronto!")
        return True
        
    except Exception as e:
        logger.error(f"❌ [Clonagem] Falha ao preparar áudio de referência: {e}")
        return False

def split_text_into_chunks(text: str, max_chars: int = 180) -> list[str]:
    """
    Divide o texto em pedaços menores baseados em pontuação natural para garantir
    que nenhuma frase enviada ao XTTSv2 ultrapasse o limite que causa travamento ou áudio truncado.
    """
    import re
    # Limpa quebras de linha extras
    text = text.replace("\n", " ").strip()
    
    # Divide pelas pontuações principais de fim de frase preservando-as
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    chunks = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
            
        if len(s) <= max_chars:
            chunks.append(s)
        else:
            # Se a frase ainda é muito longa, divide por vírgulas, dois-pontos ou travessões
            sub_parts = re.split(r'(?<=[,;:—])\s+', s)
            current_chunk = ""
            for part in sub_parts:
                part = part.strip()
                if not part:
                    continue
                if len(current_chunk) + len(part) + 1 <= max_chars:
                    if current_chunk:
                        current_chunk += " " + part
                    else:
                        current_chunk = part
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    # Caso uma parte individual ainda seja maior que o limite, divide palavra por palavra
                    if len(part) > max_chars:
                        words = part.split()
                        sub_chunk = ""
                        for word in words:
                            if len(sub_chunk) + len(word) + 1 <= max_chars:
                                if sub_chunk:
                                    sub_chunk += " " + word
                                else:
                                    sub_chunk = word
                            else:
                                if sub_chunk:
                                    chunks.append(sub_chunk)
                                sub_chunk = word
                        if sub_chunk:
                            current_chunk = sub_chunk
                    else:
                        current_chunk = part
            if current_chunk:
                chunks.append(current_chunk)
                
    return [c.strip() for c in chunks if c.strip()]

def generate_local_cloned_voice(text: str, language: str, ref_audio_path: str, output_path: str) -> bool:
    """
    Gera o áudio final clonado usando o modelo local XTTSv2.
    """
    try:
        # Importa a biblioteca TTS (deve ser instalada via pip install TTS)
        from TTS.api import TTS
        
        os.makedirs(CACHE_DIR, exist_ok=True)
        os.environ["TTS_HOME"] = CACHE_DIR # Salva os modelos na pasta do projeto
        
        # Verifica se tem GPU (Placa de vídeo NVIDIA). Se não tiver, roda na CPU (mais lento)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"🧠 [XTTSv2] Carregando modelo na {device.upper()}... (Isso pode demorar na 1ª vez)")
        
        import warnings
        warnings.filterwarnings("ignore", category=FutureWarning)

        # -------------------------------------------------------------
        # MONKEY-PATCH: Resolve o erro de weights_only=True do PyTorch 2.6+
        # -------------------------------------------------------------
        if hasattr(torch, "load"):
            _original_load = torch.load
            def _patched_load(*args, **kwargs):
                if "weights_only" not in kwargs:
                    kwargs["weights_only"] = False
                return _original_load(*args, **kwargs)
            torch.load = _patched_load
            
        import torchaudio
        import soundfile as sf
        
        def _patched_torchaudio_load(filepath, **kwargs):
            # Bypass completo do torchaudio para evitar problemas com torchcodec/FFmpeg no PyTorch 2.6
            audio_data, sr = sf.read(filepath, dtype='float32')
            if len(audio_data.shape) == 1:
                audio_data = audio_data[None, :]
            else:
                audio_data = audio_data.T
            return torch.from_numpy(audio_data), sr
            
        torchaudio.load = _patched_torchaudio_load
        # -------------------------------------------------------------

        # Inicializa o modelo
        tts = TTS(MODEL_NAME).to(device)
        
        # Mapeamento de idiomas do sistema para o formato do XTTS
        lang_map = {
            "en-US": "en",
            "pt-BR": "pt",
            "es-ES": "es",
            "fr-FR": "fr",
            "de-DE": "de"
        }
        target_lang = lang_map.get(language, "en")
        
        logger.info(f"🗣️ [XTTSv2] Gerando voz clonada em [{target_lang.upper()}]...")
        
        # Divide o roteiro completo em sentenças seguras sob o limite de caracteres
        chunks = split_text_into_chunks(text, max_chars=180)
        logger.info(f"✂️ [XTTSv2] Roteiro dividido em {len(chunks)} trechos seguros para síntese.")
        
        import tempfile
        import numpy as np
        
        chunk_files = []
        temp_dir_obj = tempfile.TemporaryDirectory()
        temp_dir = temp_dir_obj.name
        
        for i, chunk in enumerate(chunks):
            chunk_out = os.path.join(temp_dir, f"chunk_{i}.wav")
            logger.info(f"🗣️ [XTTSv2] Gerando trecho {i+1}/{len(chunks)} ({len(chunk)} caracteres): '{chunk}'")
            
            # Gera o áudio para o trecho
            tts.tts_to_file(
                text=chunk,
                speaker_wav=ref_audio_path,
                language=target_lang,
                file_path=chunk_out
            )
            
            if os.path.exists(chunk_out):
                chunk_files.append(chunk_out)
            else:
                logger.error(f"❌ Falha ao sintetizar trecho {i+1}")
                raise RuntimeError(f"Falha na síntese do trecho: {chunk}")
        
        if not chunk_files:
            logger.error("❌ Nenhum trecho foi sintetizado com sucesso.")
            return False
            
        # Combina todos os trechos de áudio gerados
        combined_audio = []
        sample_rate = None
        
        for f in chunk_files:
            data, sr = sf.read(f)
            if sample_rate is None:
                sample_rate = sr
            
            combined_audio.append(data)
            
            # Adiciona uma micro-pausa natural de 0.15s entre frases
            silence_len = int(0.15 * sr)
            if len(data.shape) > 1:
                silence = np.zeros((silence_len, data.shape[1]), dtype=data.dtype)
            else:
                silence = np.zeros(silence_len, dtype=data.dtype)
            combined_audio.append(silence)
            
        # Concatena a lista inteira
        final_audio = np.concatenate(combined_audio, axis=0)
        
        # Salva o arquivo combinado temporário em formato WAV
        temp_combined_wav = os.path.join(temp_dir, "combined_final.wav")
        sf.write(temp_combined_wav, final_audio, sample_rate)
        
        # Converte para o caminho final (ex: MP3) usando FFmpeg para compatibilidade
        logger.info(f"🎵 [Clonagem] Convertendo áudio final combinado para: {output_path}")
        convert_cmd = [
            "ffmpeg", "-y",
            "-i", temp_combined_wav,
            "-ab", "192k",
            output_path
        ]
        
        try:
            subprocess.run(convert_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"✅ [XTTSv2] Áudio final salvo com sucesso em: {output_path}")
            try: temp_dir_obj.cleanup()
            except: pass
            return True
        except Exception as e:
            logger.error(f"❌ [XTTSv2] Falha ao converter arquivo final usando FFmpeg: {e}")
            # Em caso de falha de conversão, copia o WAV bruto diretamente para o caminho final como fallback
            import shutil
            shutil.copy2(temp_combined_wav, output_path)
            try: temp_dir_obj.cleanup()
            except: pass
            return True
        
    except ImportError as e:
        logger.error(f"❌ [XTTSv2] Erro de importação de biblioteca: {e}")
        print("\nCaso a biblioteca TTS esteja instalada mas falte alguma dependência C++, verifique os logs acima.")
        print("Certifique-se de que rodou: pip install TTS torch torchvision torchaudio\n")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        logger.error(f"❌ [XTTSv2] Falha na clonagem: {e}")
        return False

# ==========================================
# TESTE RÁPIDO SE RODAR O ARQUIVO DIRETO
# ==========================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str)
    parser.add_argument("--text_file", type=str)
    parser.add_argument("--lang", type=str)
    parser.add_argument("--ref", type=str)
    parser.add_argument("--out", type=str)
    args = parser.parse_args()

    if (args.text or args.text_file) and args.ref and args.out:
        text_content = args.text
        if args.text_file and os.path.exists(args.text_file):
            try:
                with open(args.text_file, "r", encoding="utf-8") as f:
                    text_content = f.read()
            except Exception as e:
                logger.error(f"❌ [XTTSv2 CLI] Falha ao ler arquivo de texto: {e}")
                sys.exit(1)
        # Modo CLI (Subprocess)
        generate_local_cloned_voice(text_content, args.lang or "pt-BR", args.ref, args.out)
        sys.exit(0)

    print("\n" + "="*50)
    print("🎙️  CLONADOR DE VOZ LOCAL DA FÁBRICA (ZERO-SHOT)")
    print("="*50)
    
    fonte_audio = input("Link do YouTube OU caminho do áudio no PC (ENTER para Cid Moreira): ").strip()
    
    is_local_file = False
    
    if not fonte_audio:
        fonte_audio = "https://www.youtube.com/watch?v=edLTGEHQvy4"
        start_time = "00:00:04"
        duration = "00:00:14"
    elif not fonte_audio.startswith("http"):
        # É um arquivo local
        if not os.path.exists(fonte_audio):
            print(f"❌ Erro: O arquivo '{fonte_audio}' não existe no seu computador.")
            sys.exit(1)
        is_local_file = True
        print(f"✅ Arquivo local detectado: {fonte_audio}")
    else:
        # É um link do YouTube
        start_time = input("Tempo de início no formato HH:MM:SS (ex: 00:00:05): ").strip()
        if not start_time: start_time = "00:00:00"
        duration = input("Duração da fala limpa em segundos (ex: 10): ").strip()
        if not duration: duration = "10"
        
    texto = input("\nDigite a frase que o clone deve falar:\n> ").strip()
    if not texto:
        texto = "Este é um teste de voz usando a inteligência artificial. Como o áudio original estava perfeitamente limpo, o resultado será fantástico."
        
    out_wav = input("\nNome do arquivo final (ex: narração.wav) ou ENTER para padrão: ").strip()
    if not out_wav:
        out_wav = "teste_voz_clonada.wav"
        
    if is_local_file:
        import hashlib
        import subprocess
        
        # Pega a extensão e nome base
        ref_id = hashlib.md5(fonte_audio.encode()).hexdigest()[:8]
        ref_wav = f"referencia_local_{ref_id}.wav"
        
        print(f"\n🚀 Convertendo áudio local ({fonte_audio}) para o padrão da IA...")
        try:
            # Força a conversão para WAV mono 22050Hz, que é o que a IA entende
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", fonte_audio,
                "-ac", "1", "-ar", "22050",
                ref_wav
            ]
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ref_ready = True
            print(f"✅ Áudio convertido e pronto: {ref_wav}")
        except Exception as e:
            print(f"❌ Falha ao converter o áudio: {e}")
            ref_ready = False
    else:
        import hashlib
        ref_id = hashlib.md5(f"{fonte_audio}_{start_time}_{duration}".encode()).hexdigest()[:8]
        ref_wav = f"referencia_{ref_id}.wav"
        
        print(f"\n🚀 Extraindo áudio limpo de {fonte_audio} ({start_time} por {duration} segundos)...")
        ref_ready = download_reference_audio(fonte_audio, ref_wav, start_time=start_time, duration=duration)
        
    if ref_ready:
        print(f"✅ Áudio de referência pronto: {ref_wav}")
        generate_local_cloned_voice(texto, "pt-BR", ref_wav, out_wav)
        print(f"\n🎧 Verifique o arquivo final: {out_wav}")
    else:
        print("❌ Falha ao preparar áudio de referência.")
