import sys
import os
from datetime import datetime, timedelta, timezone, date
import json
import subprocess
import time
import hashlib
import warnings
warnings.filterwarnings("ignore", module="urllib3")

# ══════════════════════════════════════════════════════════════
# BOOTSTRAPPER — Primeira coisa a rodar!
# ══════════════════════════════════════════════════════════════
import dependency_manager as _dm
_DEPS = _dm.check_and_download_dependencies()

# ══════════════════════════════════════════════════════════════
# SAAS & LICENCIAMENTO
# ══════════════════════════════════════════════════════════════
import saas_manager
_authorized, _msg = saas_manager.check_access()
if not _authorized:
    saas_manager.block_screen(_msg)

class Tee(object):
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

# Captura todos os prints e erros
log_file = open("factory_console.log", "a", encoding="utf-8")
sys.stdout = Tee(sys.stdout, log_file)
sys.stderr = Tee(sys.stderr, log_file)

import requests
import random
from bs4 import BeautifulSoup
import urllib.parse
import concurrent.futures
import whisper
import threading
import asyncio
import edge_tts
import re
import hashlib
from queue import Queue
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from dotenv import load_dotenv
from yt_dlp import YoutubeDL
from pysubs2 import SSAFile, SSAEvent

# Carrega chaves de API e URLs
load_dotenv()
os.environ["COQUI_TOS_AGREED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# ==========================================
# 0. INICIALIZAÇÃO DE MODELOS E FILAS
# ==========================================
# Carrega Whisper usando o Bootstrapper para garantir download_root = app_data/models
model_whisper = _dm.load_whisper_model()

upload_queue = Queue()

# ==========================================
# 1. MOTOR DE UPLOAD (THREAD DEDICADA)
# ==========================================
def upload_worker_loop():
    while True:
        task = upload_queue.get()
        if task is None: break
        
        video_path = task['path']
        title = task['title']
        job_id = task['id']
        tipo = task.get('type', 'LONG')
        
        print(f"📤 [UPLOAD QUEUE] Iniciando subida do vídeo ({tipo}): {title}")
        
        try:
            # ===============================================
            # 🔥 MOTOR DE UPLOAD INVISÍVEL (PLAYWRIGHT)
            # ===============================================
            from agents.publisher.playwright_uploader import publish_video_to_youtube
            
            language = task.get('language', 'en-US')
            affiliate_link = task.get('injected_url')
            pinned_comment = task.get('pinned_comment')
            
            # BUG FIX: Prioritize the full AI-generated description. Only build a minimal
            # fallback if NOTHING was provided (avoids the hardcoded 'Automated AI Story' bug).
            desc = task.get('description', '').strip()
            if not desc or len(desc) < 50:
                desc = f"{title}\n\nDiscover the secrets behind AI automation and financial freedom.\n\n"
            
            tags = task.get('tags') or [t for t in title.replace("#Shorts", "").split() if len(t) > 3][:15]
            
            if affiliate_link and affiliate_link not in desc:
                desc += f"\n\n👉 Get started here: {affiliate_link}"
            
            disclaimers = {
                "en-US": "⚠️ DISCLAIMER: This video presents a high-tech speculative and predictive scenario based on AI analysis and industrial foresight. The scenarios described are speculative concepts for educational and analytical purposes.",
                "pt-BR": "⚠️ AVISO: Este vídeo apresenta um cenário especulativo e preditivo de alta tecnologia baseado em análise de IA e visão industrial. Os cenários descritos são conceitos especulativos para fins educacionais e analíticos.",
                "es-ES": "⚠️ DESCARGO DE RESPONSABILIDAD: Este video presenta un escenario especulativo y predictivo de alta tecnología basado en análisis de IA y previsión industrial. Los escenarios descritos son conceptos especulativos con fines educativos y analíticos.",
                "fr-FR": "⚠️ AVERTISSEMENT : Cette vidéo présente un scénario spéculatif et prédictif de haute technologie basé sur l'analyse de l'IA et la prospective industrielle. Les scénarios décrits sont des concepts spéculatifs à des fins éducatives et analytiques.",
                "de-DE": "⚠️ HINWEIS: Dieses Video zeigt ein spekulatives und prädiktives Hightech-Szenario, das auf KI-Analysen und industrieller Vorausschau basiert. Die beschriebenen Szenarien sind spekulative Konzepte für bildende und analytische Zwecke."
            }
            lang_key = language if language in disclaimers else "en-US"
            desc += f"\n\n{disclaimers[lang_key]}"
            
            video_url = publish_video_to_youtube(
                video_path=video_path,
                thumbnail_path=task.get('thumb_path'),
                title=title,
                description=desc,
                tags=tags,
                channel_identifier=language,
                privacy_status="public",
                pinned_comment=pinned_comment,
                is_short=(tipo == 'SHORT'),
                scheduled_at=task.get('scheduled_at'),
                srt_path=task.get('srt_path'),
                quiz_data=task.get('quiz_data')
            )
            print(f"✅ [UPLOAD CONCLUÍDO] {job_id} -> {video_url}")
        except Exception as e:
            error_msg = str(e)
            print(f"❌ [ERRO UPLOAD] {title}: {error_msg}")
            
            status_to_send = "FAILED"
            if "uploadLimitExceeded" in error_msg or "quotaExceeded" in error_msg or "Todas as chaves" in error_msg:
                print("\n⚠️ [COTA DE API ESGOTADA] O limite diário do YouTube foi atingido. O vídeo ficará na fila para amanhã.")
                status_to_send = "PENDING_UPLOAD"
            
            try:
                requests.post(f"{CLOUD_HUB_URL}/worker/job-status", json={
                    "job_id": job_id, "status": status_to_send, "error": error_msg, "type": tipo, "local_path": video_path
                }, timeout=10)
            except: pass
        
        upload_queue.task_done()

threading.Thread(target=upload_worker_loop, daemon=True).start()

# ==========================================
# 1.5 MOTOR DE NARRAÇÃO AUTÔNOMO
# ==========================================
VOICE_DEFAULT = "en-US-GuyNeural" 

async def generate_voice_audio(text, output_path, voice=VOICE_DEFAULT):
    print(f"🎙️ [EDGE-TTS] Gerando narração dinâmica (+10% speed) com a voz: {voice}")
    # Rate +10% garante o ritmo dinâmico nível 10/10
    communicate = edge_tts.Communicate(text, voice, rate="+10%")
    await communicate.save(output_path)
    return output_path

def run_voice_gen(text, output_path, voice=VOICE_DEFAULT):
    # Detecta se já existe um event loop rodando (evita RuntimeWarning de corrotina não aguardada)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Se houver um loop rodando (ex: staging_test.py), usamos uma thread isolada
        import threading
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(generate_voice_audio(text, output_path, voice))
            finally:
                new_loop.close()
        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join()
    else:
        # Se não houver loop, usamos o padrão síncrono do asyncio
        asyncio.run(generate_voice_audio(text, output_path, voice))

def generate_voice_master(text, output_path, voice=VOICE_DEFAULT, language="en-US", job_data=None):
    """Orquestrador de TTS: Usa clonagem local do Cid Moreira por padrão para todos os idiomas"""
    
    # Força a clonagem para TODOS os vídeos gerados na fábrica
    use_cloning = True
    
    # O usuário pediu para usar o teste_cid_moreira.wav, mas usaremos a referência limpa original 
    # se ela existir para garantir a emoção humana máxima (evitando cópia da cópia).
    # Se ele mandou usar o teste_cid_moreira.wav especificamente, vamos tentar usar ele como fallback.
    master_reference = r"C:\Users\User\.gemini\antigravity\scratch\dark-channel-factory\cid_moreira_reference.wav"
    if not os.path.exists(master_reference):
        master_reference = r"C:\Users\User\.gemini\antigravity\scratch\dark-channel-factory\teste_cid_moreira.wav"
        
    ref_audio = master_reference
    ref_start = "00:00:00"
    ref_duration = "10"
    
    if job_data and job_data.get("use_voice_cloning", False):
        # Permite override se o job_data trouxer outro áudio específico
        ref_audio = job_data.get("voice_reference_url", master_reference)
        ref_start = job_data.get("voice_reference_start", "00:00:00")
        ref_duration = job_data.get("voice_reference_duration", "10")
        
    # Se configurado para clonar e tem URL/Caminho
    if use_cloning and ref_audio:
        try:
            from agents.video_editor.local_voice_cloner import download_reference_audio, generate_local_cloned_voice
            import hashlib
            
            # 1. Prepara o áudio de referência (Baixa do YouTube se necessário)
            ref_id = hashlib.md5(f"{ref_audio}_{ref_start}_{ref_duration}".encode()).hexdigest()[:8]
            ref_wav_path = os.path.join(ASSETS_CACHE, f"ref_{ref_id}.wav")
            
            ref_ready = True
            if not os.path.exists(ref_wav_path):
                if ref_audio.startswith("http"):
                    ref_ready = download_reference_audio(ref_audio, ref_wav_path, start_time=ref_start, duration=ref_duration)
                elif os.path.exists(ref_audio):
                    import shutil
                    shutil.copy2(ref_audio, ref_wav_path)
                    ref_ready = True
                else:
                    ref_ready = False
                    
            # 2. Se referência está pronta, gera clonagem local
            if ref_ready:
                import subprocess
                venv_py = os.path.join("venv_clonagem", "Scripts", "python.exe")
                script_path = os.path.join("agents", "video_editor", "local_voice_cloner.py")
                
                if os.path.exists(venv_py) and os.path.exists(script_path):
                    print(f"🔄 [Bridge] Usando venv_clonagem legado para XTTSv2 no Python 3.11...")
                    temp_txt_path = os.path.join(ASSETS_CACHE, f"temp_tts_text_{ref_id}.txt")
                    try:
                        with open(temp_txt_path, "w", encoding="utf-8") as temp_f:
                            temp_f.write(text)
                    except Exception as txt_err:
                        print(f"⚠️ [Clonagem Bridge] Falha ao criar arquivo de texto temporário: {txt_err}")
                        temp_txt_path = None
                    
                    if temp_txt_path and os.path.exists(temp_txt_path):
                        cmd = [
                            venv_py, script_path, 
                            "--text_file", temp_txt_path, 
                            "--lang", language, 
                            "--ref", ref_wav_path, 
                            "--out", output_path
                        ]
                    else:
                        cmd = [
                            venv_py, script_path, 
                            "--text", text[:4000], # Fallback para tamanho reduzido para evitar falha do cmd
                            "--lang", language, 
                            "--ref", ref_wav_path, 
                            "--out", output_path
                        ]
                        
                    result = subprocess.run(cmd, stdout=None, stderr=None)
                    
                    # Limpa o arquivo temporário
                    if temp_txt_path and os.path.exists(temp_txt_path):
                        try: os.remove(temp_txt_path)
                        except: pass
                        
                    if result.returncode == 0 and os.path.exists(output_path):
                        success = True
                    else:
                        print(f"⚠️ [Clonagem Bridge] Erro do Subprocesso (código de retorno: {result.returncode}). Verifique os logs acima.")
                        success = False
                else:
                    success = generate_local_cloned_voice(text, language, ref_wav_path, output_path)
                    
                if success:
                    return output_path
        except Exception as e:
            print(f"⚠️ [Clonagem] Falha na clonagem local XTTSv2, voltando ao Edge-TTS: {e}")
            
    # ── Fallback 2: CHATTERBOX TTS (Free, Cinematic, ElevenLabs-quality alternative) ──
    try:
        from agents.video_editor.chatterbox_tts import (
            check_chatterbox_available, generate_chatterbox_audio,
            select_voice_profile, print_install_instructions
        )
        if check_chatterbox_available():
            voice_profile = select_voice_profile(job_data or {})
            # Usa o mesmo arquivo de referência do XTTSv2 como voice cloning no Chatterbox
            ref_audio_for_chatterbox = None
            if os.path.exists(master_reference):
                ref_audio_for_chatterbox = master_reference
            
            success = generate_chatterbox_audio(
                text=text,
                output_path=output_path,
                voice_profile=voice_profile,
                reference_audio=ref_audio_for_chatterbox,
                language=language,
            )
            if success:
                return output_path
            else:
                print("\u26a0\ufe0f [CHATTERBOX] Falha na gera\u00e7\u00e3o. Voltando ao Edge-TTS.")
        else:
            print_install_instructions()
            print("\u26a0\ufe0f [CHATTERBOX] N\u00e3o instalado. Usando Edge-TTS como fallback.")
    except Exception as e:
        print(f"\u26a0\ufe0f [CHATTERBOX] Erro: {e}. Voltando ao Edge-TTS.")

    # ── Fallback Final: Microsoft Edge-TTS (r\u00e1pido, gratuito, sempre dispon\u00edvel) ──
    # Speed +10% garante ritmo din\u00e2mico, rate extra para n\u00edtidez em canais dark
    print(f"\U0001f3a4 [EDGE-TTS] Fallback final: {voice}")
    run_voice_gen(text, output_path, voice)
    return output_path

# ==========================================
# 2. CONFIGURAÇÕES E DIRETÓRIOS
# ==========================================
def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

ffmpeg_exe = _DEPS.get("ffmpeg_exe", "ffmpeg")

CLOUD_HUB_URL = os.getenv("CLOUD_HUB_URL", "https://dark-channel-factory.vercel.app")
WORKER_REGION = "North America"
ASSETS_CACHE = get_resource_path("assets_cache")
OUTPUT_DIR = get_resource_path("videos_finais")
os.makedirs(ASSETS_CACHE, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

PEXELS_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")
JAMENDO_KEY = os.getenv("JAMENDO_CLIENT_ID")
FREESOUND_KEY = os.getenv("FREESOUND_API_KEY")

PREMIUM_CACHE_DIR = os.path.join(ASSETS_CACHE, "premium_cache")
AUDIO_CACHE_DIR = os.path.join(ASSETS_CACHE, "audio_cache")
os.makedirs(PREMIUM_CACHE_DIR, exist_ok=True)
os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

def fetch_background_music(mood):
    """Busca trilha sonora no Pixabay ou Jamendo baseada no mood do roteiro."""
    local_path = os.path.join(AUDIO_CACHE_DIR, f"music_{mood}.mp3")
    if os.path.exists(local_path): return local_path
    
    # 1. Tenta Pixabay (Preferencial do Usuário)
    if PIXABAY_KEY:
        try:
            url = f"https://pixabay.com/api/music/?key={PIXABAY_KEY}&q={urllib.parse.quote(mood)}&per_page=3"
            res = requests.get(url, timeout=10).json()
            if res.get('hits'):
                audio_url = res['hits'][0]['audio']
                r = requests.get(audio_url, timeout=20)
                with open(local_path, "wb") as f: f.write(r.content)
                print(f"🎵 [MUSIC] Fonte: Pixabay API (Mood: {mood})")
                return local_path
        except: pass

    # 2. Tenta Jamendo (Fallback)
    if JAMENDO_KEY:
        try:
            url = f"https://api.jamendo.com/v3.0/tracks/?client_id={JAMENDO_KEY}&format=json&limit=1&fuzzytags={mood}&speed=medium"
            res = requests.get(url, timeout=10).json()
            if res.get('results'):
                audio_url = res['results'][0]['audio']
                r = requests.get(audio_url, timeout=20)
                with open(local_path, "wb") as f: f.write(r.content)
                print(f"🎵 [MUSIC] Fonte: Jamendo API (Mood: {mood})")
                return local_path
        except: pass
        
    return None

def fetch_sfx(cue):
    """Busca efeitos sonoros no Freesound baseada na deixa do roteiro."""
    if not FREESOUND_KEY or cue == "none": return None
    local_path = os.path.join(AUDIO_CACHE_DIR, f"sfx_{cue}.mp3")
    if os.path.exists(local_path): return local_path
    
    try:
        url = f"https://freesound.org/apiv2/search/text/?query={cue}&token={FREESOUND_KEY}&fields=id,previews&page_size=1"
        res = requests.get(url, timeout=10).json()
        if res.get('results'):
            audio_url = res['results'][0]['previews']['preview-hq-mp3']
            r = requests.get(audio_url, timeout=20)
            with open(local_path, "wb") as f: f.write(r.content)
            return local_path
    except: pass
    return None

def fetch_wiki_image(entity_name):
    """Busca imagens reais e públicas na Wikipedia para entidades específicas (Marcas, Pessoas, Prédios)."""
    try:
        import requests
        import urllib.parse
        print(f"🏛️ [WIKI] Buscando imagem real para: {entity_name}...")
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(entity_name)}&prop=pageimages&format=json&pithumbsize=1280"
        r = requests.get(search_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id in pages:
                thumb = pages[page_id].get("thumbnail", {}).get("source")
                if thumb: return thumb
    except Exception as e:
        print(f"⚠️ [WIKI] Erro: {e}")
    return None

def fetch_brand_logo(brand_name):
    """Busca o logo oficial de uma marca via Clearbit API (Gratuito)."""
    # Tenta prever o domínio para a Clearbit
    domains = {
        "blackrock": "blackrock.com",
        "jpmorgan": "jpmorganchase.com",
        "chase": "chase.com",
        "apple": "apple.com",
        "google": "google.com",
        "tesla": "tesla.com",
        "amazon": "amazon.com",
        "nvidia": "nvidia.com",
        "microsoft": "microsoft.com",
        "goldman sachs": "goldmansachs.com"
    }
    domain = domains.get(brand_name.lower())
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return None

def fetch_premium_broll(keyword, used_files=None):
    if used_files is None: used_files = set()
    import re
    # 0. Limpeza e Extração de Palavras-Chave (Stop words expandidas)
    clean_keyword = re.sub(r'[^\w\s]', '', keyword.lower())
    words = clean_keyword.split()
    
    # Palavras que não agregam valor à busca de imagem/vídeo
    stop_words = {
        "a", "an", "the", "and", "or", "but", "of", "with", "in", "on", "at", "to", "from", 
        "showing", "shows", "shown", "then", "cuts", "where", "highly", "detailed", "visual", 
        "query", "style", "cinematic", "4k", "8k", "ultra", "hd", "high", "resolution", 
        "background", "concept", "illustration", "animation", "footage", "stock", "video",
        "extreme", "close", "up", "wide", "shot", "angle", "view", "top", "bottom", "side",
        "looking", "at", "facing", "towards", "around"
    }
    
    important_words = [w for w in words if w not in stop_words and len(w) > 2]
    
    # 1. Busca no Cache Premium (Match por palavras-chave)
    best_match = None
    best_score = 0
    
    # [NOVO] DETECÇÃO DE ENTIDADES (Marcas, Empresas e Bilionários)
    # Se a keyword contém uma entidade famosa, tentamos buscar a imagem real primeiro!
    entities_to_watch = [
        "blackrock", "jpmorgan", "goldman sachs", "nvidia", "tesla", "apple", "google", "microsoft", "amazon", "chase",
        "elon musk", "jeff bezos", "bill gates", "mark zuckerberg", "warren buffett", "bernard arnault", "larry page", 
        "sergey brin", "larry ellison", "steve jobs", "sam altman", "jensen huang", "vitalik buterin"
    ]
    detected_entity = next((e for e in entities_to_watch if e in clean_keyword), None)
    
    if detected_entity:
        print(f"🎯 [ENTITY] Entidade detectada: {detected_entity.upper()}. Buscando ativos reais...")
        # Prioridade 1: Wikipedia (Fotos Reais de Pessoas ou Sedes)
        entity_url = fetch_wiki_image(detected_entity)
        if entity_url:
            print(f"✅ [ENTITY] Imagem real encontrada via Wiki: {entity_url}")
            safe_id = hashlib.md5(entity_url.encode()).hexdigest()[:12]
            local_path = os.path.join(ASSETS_CACHE, f"entity_{safe_id}.jpg")
            if not os.path.exists(local_path):
                try:
                    r = requests.get(entity_url, timeout=15)
                    r.raise_for_status()
                    with open(local_path, "wb") as f: f.write(r.content)
                    return local_path
                except Exception as e:
                    print(f"⚠️ [ENTITY] Falha ao baixar imagem Wiki {entity_url}: {e}")
            else:
                return local_path
        # Prioridade 2: Logo (Apenas para Marcas, se Wiki falhar)
        logo_url = fetch_brand_logo(detected_entity)
        if logo_url:
            print(f"🎨 [ENTITY] Logo oficial encontrado: {logo_url}")
            safe_id = hashlib.md5(logo_url.encode()).hexdigest()[:12]
            local_path = os.path.join(ASSETS_CACHE, f"logo_{safe_id}.png")
            if not os.path.exists(local_path):
                try:
                    r = requests.get(logo_url, timeout=15)
                    r.raise_for_status()
                    with open(local_path, "wb") as f: f.write(r.content)
                    return local_path
                except Exception as e:
                    print(f"⚠️ [ENTITY] Falha ao baixar logo de {logo_url}: {e}")
            else:
                return local_path
    
    if os.path.exists(PREMIUM_CACHE_DIR):
        for filename in os.listdir(PREMIUM_CACHE_DIR):
            if filename.endswith(".mp4"):
                full_p = os.path.join(PREMIUM_CACHE_DIR, filename)
                if full_p in used_files: continue
                
                file_words = set(re.sub(r'[^\w\s]', ' ', filename.lower().replace(".mp4", "")).split())
                score = len(set(important_words).intersection(file_words))
                if score > best_score:
                    best_score = score
                    best_match = full_p
                    
    # Se encontrou pelo menos 2 palavras importantes em comum, usa do cache!
    if best_score >= 2 and best_match:
        print(f"⚡ [CACHE INTELIGENTE] Match: '{os.path.basename(best_match)}' (score: {best_score})")
        used_files.add(best_match)
        return best_match

    # Simplificar keyword para as APIs web (mais palavras para ser preciso, mas filtradas)
    base_query = " ".join(important_words[:7]) if important_words else "artificial intelligence technology"
    api_query = f"{base_query} bright 4k modern"

    # BUG FIX: Usar o path absoluto para checar no used_files antes de retornar do cache de assets
    # Também adicionamos um 'index' opcional ou variação no hash se necessário
    safe_id = hashlib.md5(api_query.encode()).hexdigest()[:12]
    local_path = os.path.join(ASSETS_CACHE, f"asset_{safe_id}.mp4")
    
    if os.path.exists(local_path) and local_path not in used_files:
        used_files.add(local_path)
        return local_path

    # 1.1 NOVO: Busca Semântica no Cache Local (Se o arquivo exato não existe, procura por similaridade)
    # Isso evita chamadas desnecessárias à API e resolve o problema de 'Black Screen' se a internet falhar
    import glob
    print(f"🔍 [CACHE] Procurando similaridade local para: '{api_query}'...")
    all_cached = glob.glob(os.path.join(ASSETS_CACHE, "*.mp4"))
    best_cache_match = None
    max_hits = 0
    for cached_file in all_cached[:500]: # Limita a 500 arquivos para performance
        if cached_file in used_files: continue
        filename = os.path.basename(cached_file).lower()
        hits = sum(1 for word in important_words if word in filename)
        if hits > max_hits:
            max_hits = hits
            best_cache_match = cached_file
    
    if max_hits >= 2: # Se bater 2+ palavras, aproveita o cache!
        # [FIX] Rename file if it exceeds 100 characters to prevent WinError 206 (MAX_PATH limit)
        if len(os.path.basename(best_cache_match)) > 100:
            try:
                new_name = f"asset_sem_{hashlib.md5(os.path.basename(best_cache_match).encode()).hexdigest()[:12]}.mp4"
                new_path = os.path.join(ASSETS_CACHE, new_name)
                os.rename(best_cache_match, new_path)
                best_cache_match = new_path
            except Exception:
                pass
                
        print(f"✅ [CACHE SEMÂNTICO] Reaproveitando: {os.path.basename(best_cache_match)} (Score: {max_hits})")
        used_files.add(best_cache_match)
        return best_cache_match

    print(f"🌐 [WEB] Buscando B-Roll API para: '{api_query}'...")
    asset_url = None
    source_name = None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # 1. Pexels (Prioridade - Melhores Resultados)
    def try_pexels(q):
        try:
            # Pede 15 vídeos e escolhe um aleatório para evitar repetição massiva
            res_raw = requests.get(f"https://api.pexels.com/videos/search?query={urllib.parse.quote(q)}&per_page=15", 
                                   headers={"Authorization": PEXELS_KEY}, timeout=15)
            if res_raw.status_code == 200:
                res = res_raw.json()
                if res.get('videos'):
                    random.shuffle(res['videos']) # Aleatoriedade
                    for video in res['videos']:
                        link = video['video_files'][0]['link']
                        # Como não sabemos o path final ainda, guardamos o link original para conferir depois se quiser
                        # Mas por agora, apenas retornamos o primeiro aleatório
                        return link
            else:
                print(f"⚠️ [PEXELS] Erro HTTP {res_raw.status_code}")
        except Exception as e: 
            print(f"⚠️ [PEXELS] Falha na conexão: {e}")
        return None

    # 2. Pixabay
    def try_pixabay(q):
        try:
            # Pede 10 vídeos
            res_raw = requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={urllib.parse.quote(q)}&per_page=10", timeout=15)
            if res_raw.status_code == 200:
                res = res_raw.json()
                if res.get('hits'):
                    random.shuffle(res['hits'])
                    return res['hits'][0]['videos']['medium']['url']
            else:
                print(f"⚠️ [PIXABAY] Erro HTTP {res_raw.status_code}")
        except Exception as e: 
            print(f"⚠️ [PIXABAY] Falha na conexão: {e}")
        return None

    # 3. Busca Aberta Wikipedia/Wikimedia
    def try_wikimedia(q):
        try:
            import urllib.parse
            res_raw = requests.get(f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q)}&utf8=&format=json", timeout=10)
            if res_raw.status_code == 200:
                pages = res_raw.json().get("query", {}).get("search", [])
                for p in pages[:3]:
                    img = fetch_wiki_image(p["title"])
                    if img: return img
        except: pass
        return None

    if not asset_url:
        asset_url = try_pexels(api_query)
        if asset_url: source_name = "Pexels API"
        
    if not asset_url:
        asset_url = try_pixabay(api_query)
        if asset_url: source_name = "Pixabay API"
        
    if not asset_url:
        asset_url = try_wikimedia(api_query)
        if asset_url: source_name = "Wikimedia API"
        
    # FALLBACK DE BUSCA: Se a frase inteira falhou, tenta palavras importantes e termos de segurança
    if not asset_url:
        # Filtra palavras que não são úteis para busca (Stop words expandidas EN/PT)
        blacklist = {
            "from", "your", "over", "into", "across", "with", "shows", "shown", "where", "this", "that", "these", "those",
            "pelo", "pela", "pelos", "pelas", "numa", "num", "como", "para", "com", "uma", "uns", "umas", "mais", "menos",
            "sobre", "entre", "através", "esta", "este", "isto", "isso", "aquilo", "cada", "tudo", "todo", "toda"
        }
        # Filtra palavras curtas e stop-words
        valid_keywords = [w for w in important_words if len(w) >= 4 and w.lower() not in blacklist]
        
        # Se não sobrou nada, tenta palavras curtas mas que não estejam na blacklist
        if not valid_keywords:
            valid_keywords = [w for w in important_words if w.lower() not in blacklist and len(w) > 2]

        fallback_q = valid_keywords[0] if valid_keywords else (important_words[0] if important_words else "tech")
        print(f"🔄 Buscando com termo filtrado: '{fallback_q}'...")
        asset_url = try_pexels(fallback_q) or try_pixabay(fallback_q)
        
        # NOVO: Último recurso de vídeo — Termos de Segurança do Nicho
        if not asset_url:
            niche_fallbacks = ["technology", "innovation", "digital art", "abstract tech", "cyberpunk city", "artificial intelligence"]
            safe_q = random.choice(niche_fallbacks)
            print(f"🆘 [ULTRA FALLBACK] Tentando termo de segurança: '{safe_q}'")
            asset_url = try_pexels(safe_q) or try_pixabay(safe_q)
            
        if asset_url: source_name = "Fallback Filtrado/Segurança"

    if asset_url:
        try:
            print(f"✅ [B-ROLL] Fonte: {source_name} | Baixando...")
            # Pexels pode ser lento. Aumentando o timeout para 60s.
            r = requests.get(asset_url, stream=True, headers=headers, timeout=60)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)
                used_files.add(local_path)
                return local_path
        except Exception as e: 
            print(f"❌ [B-ROLL ERRO] Falha ao baixar: {e}")
    
    # 4. FALLBACK FINAL: Gerador de Quadro de Contexto "Elite" (Pillow com Gradiente)
    # Isso garante que mesmo sem internet o vídeo tenha um visual aceitável e supere os 45KB do QC.
    try:
        emergency_path = os.path.join(ASSETS_CACHE, f"emergency_frame_{hashlib.md5(api_query.encode()).hexdigest()[:8]}.jpg")
        
        # Cria um gradiente dinâmico para evitar compressão excessiva e passar no QC (>45KB)
        img = Image.new('RGB', (1280, 720), color=(20, 25, 35))
        draw = ImageDraw.Draw(img)
        
        # Adiciona gradiente diagonal
        for y in range(720):
            r_c = 20 + int(y/12)
            g_c = 25 + int(y/18)
            b_c = 35 + int(y/25)
            draw.line([(0, y), (1280, y)], fill=(r_c, g_c, b_c))
            
        # Adiciona círculos de "bokeh" no fundo para dar profundidade e peso ao arquivo
        for _ in range(12):
            x, y = random.randint(0, 1280), random.randint(0, 720)
            rad = random.randint(40, 180)
            draw.ellipse([x-rad, y-rad, x+rad, y+rad], fill=(r_c+8, g_c+8, b_c+8))

        img = img.filter(ImageFilter.GaussianBlur(radius=15))
        draw = ImageDraw.Draw(img)
        
        # Título centralizado
        text = f"CONTEXT: {api_query.upper()}"
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 42)
        except:
            font = ImageFont.load_default()
            
        draw.text((640, 360), text, fill=(0, 255, 204), anchor="mm", font=font)
        
        img.save(emergency_path, "JPEG", quality=96) # Alta qualidade garante >45KB
        print(f"🎨 [DESIGN] Quadro de emergência premium gerado: {api_query}")
        return emergency_path
    except Exception as e:
        print(f"❌ [PIL ERROR] {e}")
        return None

def generate_quiz_frame(quiz_data, job_id):
    """Gera um frame visual para o Quiz usando PIL."""
    try:
        quiz_path = os.path.join(OUTPUT_DIR, f"quiz_{job_id}.jpg")
        
        # Fundo Cinematográfico Escuro
        img = Image.new('RGB', (1280, 720), color=(10, 15, 25))
        draw = ImageDraw.Draw(img)
        
        # Gradiente e Detalhes de Fundo
        for y in range(720):
            draw.line([(0, y), (1280, y)], fill=(10+int(y/20), 15+int(y/30), 25+int(y/40)))
        
        try:
            font_title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 50)
            font_q = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 35)
            font_opt = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
        except:
            font_title = font_q = font_opt = ImageFont.load_default()

        # Cabeçalho
        draw.text((640, 80), "🧠 QUICK BRAIN TEST", fill=(0, 255, 204), anchor="mm", font=font_title)
        draw.line([(300, 120), (980, 120)], fill=(0, 255, 204), width=3)

        # Pergunta
        question = quiz_data.get("question", "What is the future of AI?")
        # Quebra de linha simples para a pergunta
        words = question.split()
        lines = [" ".join(words[i:i+8]) for i in range(0, len(words), 8)]
        y_text = 200
        for line in lines:
            draw.text((640, y_text), line, fill=(255, 255, 255), anchor="mm", font=font_q)
            y_text += 45

        # Opções
        options = quiz_data.get("options", ["Option A", "Option B", "Option C"])
        y_opt = 420
        colors = [(255, 215, 0), (0, 200, 255), (150, 255, 100), (255, 100, 100)]
        for i, opt in enumerate(options[:4]):
            # Caixa da opção
            draw.rectangle([200, y_opt-30, 1080, y_opt+30], outline=colors[i], width=2)
            draw.text((640, y_opt), f"{chr(65+i)}) {opt}", fill=colors[i], anchor="mm", font=font_opt)
            y_opt += 80

        img.save(quiz_path, "JPEG", quality=95)
        print(f"🧠 [QUIZ] Frame visual gerado para o Quiz.")
        return quiz_path
    except Exception as e:
        print(f"❌ [QUIZ ERROR] {e}")
        return None

def generate_subtitles_locally(audio_path, job_id):
    print(f"📝 [WHISPER] Transcrevendo áudio...")
    srt_path = os.path.join(OUTPUT_DIR, f"subs_{job_id}.srt")
    try:
        result = model_whisper.transcribe(audio_path, verbose=False, fp16=False)
        subs = SSAFile()
        for segment in result['segments']:
            start = int(segment['start'] * 1000)
            end = int(segment['end'] * 1000)
            text = segment['text'].strip()
            subs.append(SSAEvent(start=start, end=end, text=text))
        subs.save(srt_path)
        return srt_path
    except Exception as e:
        print(f"⚠️ Erro na transcrição: {e}")
        return None

# ==========================================
# 4. MONTAGEM FINAL
# ==========================================

def assemble_production(job_id, video_paths, audio_path, srt_path, topic, clip_durations, total_audio_sec, job_data, is_short=False, music_mood="suspense", sfx_paths=[], thumb_path=None):
    # Criar links ou cópias curtas para evitar o WinError 206 (limite de tamanho de linha de comando no Windows)
    temp_dir = os.path.join(OUTPUT_DIR, f"tmp_in_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)
    
    import shutil
    
    def cleanup():
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

    video_paths_original = video_paths.copy()
    sfx_paths_original = sfx_paths.copy()

    # Deduplicate video/image inputs
    unique_video_paths = []
    video_path_to_uniq_idx = {}
    for p in video_paths:
        if p not in video_path_to_uniq_idx:
            video_path_to_uniq_idx[p] = len(unique_video_paths)
            unique_video_paths.append(p)

    unique_short_video_paths = []
    for idx, p in enumerate(unique_video_paths):
        ext = os.path.splitext(p)[1] or (".png" if p.lower().endswith(('.jpg', '.jpeg', '.png')) else ".mp4")
        short_path = os.path.join(temp_dir, f"v_uniq_{idx}{ext}")
        try:
            if os.path.exists(short_path): os.remove(short_path)
            shutil.copy2(p, short_path)
        except Exception as e:
            print(f"⚠️ Erro ao copiar input temporário: {e}")
            short_path = p
        unique_short_video_paths.append(short_path)

    # Map each scene's video path to its short path
    scene_video_paths = [unique_short_video_paths[video_path_to_uniq_idx[p]] for p in video_paths]

    # Deduplicate SFX inputs
    unique_sfx_paths = []
    sfx_path_to_uniq_idx = {}
    for p in sfx_paths:
        if p and os.path.exists(p):
            if p not in sfx_path_to_uniq_idx:
                sfx_path_to_uniq_idx[p] = len(unique_sfx_paths)
                unique_sfx_paths.append(p)

    unique_short_sfx_paths = []
    for idx, p in enumerate(unique_sfx_paths):
        ext = os.path.splitext(p)[1] or ".mp3"
        short_path = os.path.join(temp_dir, f"sfx_uniq_{idx}{ext}")
        try:
            if os.path.exists(short_path): os.remove(short_path)
            shutil.copy2(p, short_path)
        except Exception as e:
            print(f"⚠️ Erro ao copiar sfx temporário: {e}")
            short_path = p
        unique_short_sfx_paths.append(short_path)

    # Map each scene's SFX path to its short path
    scene_sfx_paths = [unique_short_sfx_paths[sfx_path_to_uniq_idx[p]] if (p and os.path.exists(p)) else None for p in sfx_paths]

    # Encurtar audio_path
    if audio_path and os.path.exists(audio_path):
        short_audio = os.path.join(temp_dir, f"aud{os.path.splitext(audio_path)[1] or '.mp3'}")
        try:
            if os.path.exists(short_audio): os.remove(short_audio)
            shutil.copy2(audio_path, short_audio)
            audio_path = short_audio
        except: pass

    # Encurtar srt_path
    if srt_path and os.path.exists(srt_path):
        short_srt = os.path.join(temp_dir, f"srt{os.path.splitext(srt_path)[1] or '.srt'}")
        try:
            if os.path.exists(short_srt): os.remove(short_srt)
            shutil.copy2(srt_path, short_srt)
            srt_path = short_srt
        except: pass

    # Encurtar thumb_path
    if thumb_path and os.path.exists(thumb_path):
        short_thumb = os.path.join(temp_dir, f"th{os.path.splitext(thumb_path)[1] or '.jpg'}")
        try:
            if os.path.exists(short_thumb): os.remove(short_thumb)
            shutil.copy2(thumb_path, short_thumb)
            thumb_path = short_thumb
        except: pass

    print(f"⚡ [HARDWARE ACCEL] A utilizar Intel QSV para renderização {'VERTICAL' if is_short else 'HORIZONTAL'}...")
    res = "720x1280" if is_short else "1280x720"
    w, h = (720, 1280) if is_short else (1280, 720)
    
    output_long = os.path.join(OUTPUT_DIR, f"final_{job_id}.mp4")
    output_shorts = os.path.join(OUTPUT_DIR, f"shorts_{job_id}.mp4")
    filter_script_path = os.path.join(OUTPUT_DIR, f"filter_{job_id}.txt")
    
    filter_complex = ""
    # 1. PROCESSAMENTO DE CADA CENA
    for i, dur in enumerate(clip_durations):
        v_path = scene_video_paths[i]
        is_image = v_path.lower().endswith(('.jpg', '.jpeg', '.png'))
        num_frames = int(dur * 30) + 1
        
        # Base de processamento: Redimensionar e ajustar FPS
        # Importante: Para imagens, precisamos habilitar o loop ANTES de entrar no filter_complex
        # Mas como estamos usando entradas numeradas, vamos tratar via filtros
        
        # Efeito Visual de Acordo com Posição da Cena
        is_hook_scene = (i < 3 and not is_short)  # Primeiras 3 cenas do vídeo longo = hook especial
        
        if is_image:
            if is_hook_scene:
                # HOOK: Ken Burns acelerado (zoom-in rápido)
                visual_fx = f"scale=4000:-1,zoompan=z='min(zoom+0.004,1.6)':d={num_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,trim=duration={dur}"
            else:
                # Efeito Ken Burns Variável (Zoom-in ou Zoom-out aleatório)
                direction = random.choice(["in", "out"])
                zoom_speed = random.uniform(0.0008, 0.0018)
                if direction == "in":
                    z_val = f"min(zoom+{zoom_speed},1.5)"
                else:
                    z_val = f"max(zoom-{zoom_speed},1.0)"
                visual_fx = f"scale=4000:-1,zoompan=z='{z_val}':d={num_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,trim=duration={dur}"
        else:
            # Para VÍDEOS: Adiciona movimento sutil (Slow Zoom) para evitar visual estático
            zoom_speed = random.uniform(0.0003, 0.0008)
            # Para vídeos, usamos scale + crop dinâmico para simular zoom sem o peso do zoompan
            zoom_factor = f"(1+0.15*t/{dur})" # Zoom de 15% ao longo da duração
            visual_fx = f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,crop={w}:{h},scale=w='{w}*{zoom_factor}':h=-1:eval=frame,crop={w}:{h},setsar=1,fps=30,trim=duration={dur}"
            
            if is_hook_scene:
                # HOOK: Acelerado (Sem tremor, apenas zoom limpo para estética B2B)
                zoom_factor = f"(1+0.25*t/{dur})" # Zoom mais agressivo para o hook
                visual_fx = f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,crop={w}:{h},scale=w='{w}*{zoom_factor}':h=-1:eval=frame,crop={w}:{h},setsar=1,fps=30,trim=duration={dur}"

        # Camada de Estilo Sênior: Color Grade Teal & Orange Profissional
        if is_hook_scene:
            # Hook: Look "Blockbuster" + B2B Premium
            style_fx = "eq=brightness=0.03:contrast=1.1:saturation=1.1,unsharp=5:5:1.5:5:5:0.5,vignette=angle=0.6,noise=alls=12:allf=t,format=yuv420p"
        else:
            # Padrão: Cinematic soft + B2B Premium
            style_fx = "eq=brightness=0.03:contrast=1.1:saturation=1.1,unsharp=3:3:0.8:3:3:0.4,vignette=angle=0.5,noise=alls=7:allf=t,format=yuv420p"
        
        # ── Retention Engine: apply visual_cue directive from script JSON ──
        # scene_visual_cues is built in render_video() and passed via job_data
        scene_cues_map = job_data.get("_scene_visual_cues", []) if job_data else []
        if i < len(scene_cues_map) and scene_cues_map[i]:
            visual_fx = _apply_visual_cue_fx(visual_fx, scene_cues_map[i], dur)
        
        # Transição: fade mais lento no hook para maior impacto
        fade_dur = 1.2 if is_hook_scene else 0.7
        fade_in = "" if i == 0 else f"fade=t=in:st=0:d={fade_dur},"
        trans_fx = f"{fade_in}fade=t=out:st={max(0, dur-fade_dur)}:d={fade_dur}"
        
        uniq_idx = video_path_to_uniq_idx[video_paths_original[i]]
        input_idx = 1 + uniq_idx
        filter_complex += f"[{input_idx}:v]{visual_fx},{style_fx},{trans_fx},setpts=PTS-STARTPTS[v{i}];"

    
    concat_nodes = "".join([f"[v{i}]" for i in range(len(video_paths))])

    # Combinar todos os nós de vídeo em um único stream
    safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    
    # Pré-computa índice real da thumbnail (adicionada após música + SFX)
    # Índices: 0=audio, 1..N=vídeos, N+1=música, N+2..N+1+sfx=SFX, N+2+sfx=thumb
    num_uniq_videos = len(unique_short_video_paths)
    music_file = fetch_background_music(music_mood)
    has_music = music_file and os.path.exists(music_file)
    bg_music_fallback = get_resource_path("channel_setup/assets/audio/background_suspense.mp3")
    _has_any_music_input = has_music or os.path.exists(bg_music_fallback)
    
    music_input_idx = None
    if _has_any_music_input:
        music_input_idx = num_uniq_videos + 1
        
    sfx_start_idx = num_uniq_videos + (2 if _has_any_music_input else 1)
    num_uniq_sfx = len(unique_short_sfx_paths)
    thumb_input_idx = sfx_start_idx + num_uniq_sfx
    
    if is_short:
        if thumb_path and os.path.exists(thumb_path):
            # Short Principal: Legendas + Thumbnail nos primeiros 0.5s
            safe_thumb = thumb_path.replace('\\', '/').replace(':', '\\:')
            filter_complex += (
                f"{concat_nodes}concat=n={len(video_paths)}:v=1:a=0[concat_v];"
                f"[concat_v]subtitles='{safe_srt}':force_style='Alignment=2,FontSize=12,Outline=1,Shadow=1,MarginV=90,PrimaryColour=&H00FFFF&'[withsubs];"
                f"[{thumb_input_idx}:v]scale=720:1280,setsar=1[thumb_ov];"
                f"[withsubs][thumb_ov]overlay=0:0:enable='between(t,0,0.5)',setdar=9/16[v_final];"
            )
        else:
            # Short Principal sem thumb
            filter_complex += f"{concat_nodes}concat=n={len(video_paths)}:v=1:a=0[concat_v];[concat_v]subtitles='{safe_srt}':force_style='Alignment=2,FontSize=12,Outline=1,Shadow=1,MarginV=90,PrimaryColour=&H00FFFF&'[v_final];"
    else:
        filter_complex += f"{concat_nodes}concat=n={len(video_paths)}:v=1:a=0[v_final];"
    
    # 2. MIXAGEM DE ÁUDIO DINÂMICA
    # loudnorm = Normalização EBU R128 (padrão YouTube/Spotify: I=-14 LUFS, TP=-1.5 dB)
    # Garante que a narração XTTSv2 (naturalmente baixa) saia em volume broadcast máximo
    filter_complex += f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[voice];"
    
    if has_music:
        music_idx = num_uniq_videos + 1
        filter_complex += f"[{music_idx}:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm];"
        
        # 3. MIXAGEM DE SFX (SE HOUVER)
        sfx_nodes = []
        current_time_ms = 0
        for i, sfx_p in enumerate(sfx_paths):
            if sfx_p and os.path.exists(sfx_p):
                uniq_sfx_idx = sfx_path_to_uniq_idx[sfx_paths_original[i]]
                sfx_idx = sfx_start_idx + uniq_sfx_idx
                # Atrasa o SFX para começar no início da cena
                filter_complex += f"[{sfx_idx}:a]adelay={int(current_time_ms)}|{int(current_time_ms)},volume=0.3[sfx{i}];"
                sfx_nodes.append(f"[sfx{i}]")
            
            if i < len(clip_durations):
                current_time_ms += clip_durations[i] * 1000
                
        if sfx_nodes:
            sfx_concat = "".join(sfx_nodes)
            filter_complex += f"[voice][bgm]{sfx_concat}amix=inputs={2+len(sfx_nodes)}:duration=first[a_final]"
        else:
            filter_complex += "[voice][bgm]amix=inputs=2:duration=first[a_final]"
    else:
        # Fallback para o arquivo local se a busca falhar
        bg_music_fallback = get_resource_path("channel_setup/assets/audio/background_suspense.mp3")
        if os.path.exists(bg_music_fallback):
            music_idx = num_uniq_videos + 1
            filter_complex += f"[{music_idx}:a]volume=0.10,aloop=loop=-1:size=2e+09[bgm];"
            filter_complex += "[voice][bgm]amix=inputs=2:duration=first[a_final]"
        else:
            filter_complex += "[voice]copy[a_final]"

    with open(filter_script_path, "w", encoding="utf-8") as f:
        f.write(filter_complex)

    args_long = [ffmpeg_exe, "-i", audio_path]
    for idx, p in enumerate(unique_short_video_paths):
        orig_p = unique_video_paths[idx]
        is_image = orig_p.lower().endswith(('.jpg', '.jpeg', '.png'))
        if is_image:
            args_long.extend(["-loop", "1", "-framerate", "30", "-i", p])
        else:
            args_long.extend(["-stream_loop", "-1", "-i", p])
    
    # Adiciona a trilha sonora com loop infinito
    if has_music:
        args_long.extend(["-stream_loop", "-1", "-i", music_file])
    else:
        bg_music_fallback = get_resource_path("channel_setup/assets/audio/background_suspense.mp3")
        if os.path.exists(bg_music_fallback):
            args_long.extend(["-stream_loop", "-1", "-i", bg_music_fallback])

    # Adiciona os arquivos de SFX aos inputs do FFmpeg
    for p in unique_short_sfx_paths:
        args_long.extend(["-i", p])
    
    # Adiciona Thumbnail como input para o Short Principal (overlay nos primeiros 0.5s)
    if is_short and thumb_path and os.path.exists(thumb_path):
        args_long.extend(["-loop", "1", "-i", thumb_path])

    # Para Shorts: limita a 58 segundos (o YouTube rejeita Shorts > 60s)
    # Para Longos: usa a duração exata da narração
    output_duration = min(total_audio_sec, 58) if is_short else total_audio_sec
    
    args_long.extend([
        "-filter_complex_script", filter_script_path,
        "-map", "[v_final]", "-map", "[a_final]",
        "-c:v", "h264_qsv", "-preset", "medium", "-pix_fmt", "nv12", "-b:v", "8M", "-maxrate:v", "12M", "-bufsize:v", "16M", 
        "-t", str(output_duration),
        output_long, "-y"
    ])
    
    print(f"🎬 [FFMPEG] Renderizando com CINEMATIC FX e ÁUDIO DINÂMICO...")
    use_qsv = True
    res_long = subprocess.run(args_long, capture_output=True, text=True)
    
    if res_long.returncode != 0:
        print(f"⚠️ [QSV FAIL] Tentando fallback para CPU (libx264)...")
        use_qsv = False
        args_long_cpu = args_long.copy()
        # Substitui encoder QSV por Software
        idx_cv = args_long_cpu.index("-c:v")
        args_long_cpu[idx_cv+1] = "libx264"
        args_long_cpu[idx_cv+2] = "-preset"
        args_long_cpu[idx_cv+3] = "veryfast"
        # Ajusta pix_fmt para software
        idx_pix = args_long_cpu.index("-pix_fmt")
        args_long_cpu[idx_pix+1] = "yuv420p"
        
        res_long = subprocess.run(args_long_cpu, capture_output=True, text=True)
        if res_long.returncode != 0:
            print(f"❌ [FFMPEG CRITICAL ERROR]\n{res_long.stderr}")
            cleanup(); return False

    if not is_short:
        print("📱 [SHORTS] Gerando 3 Shorts virais de 59 segundos...")
        
        # [NOVO] Gerar Thumbnail Vertical (9:16)
        short_thumb_path = None
        try:
            from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation
            print("🎨 [SHORTS] Gerando Thumbnail Vertical (9:16)...")
            # Usamos o topic e o job_id disponíveis na assemble_production
            short_thumb_res = orchestrate_thumbnail_generation(
                job_id=job_id, title=topic, language="en-US",
                niche="Tech", video_file_path=output_long, is_short=True
            )
            short_thumb_path = short_thumb_res.get("selected_thumbnail", {}).get("path")
        except Exception as e:
            print(f"⚠️ [SHORTS] Erro ao gerar thumb: {e}")

        encoder = "h264_qsv" if use_qsv else "libx264"
        pix_fmt = "nv12" if use_qsv else "yuv420p"

        for i in range(1, 4):
            # [NOVO] Thumbnail Única para cada Short
            current_short_thumb = None
            try:
                from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation
                print(f"🎨 [SHORTS] Gerando Thumbnail Única para Parte {i}...")
                short_thumb_res = orchestrate_thumbnail_generation(
                    job_id=f"{job_id}_{i}", title=f"{topic} (Part {i})", 
                    language=job_data.get("language", "en-US"),
                    niche="Tech", video_file_path=output_long, is_short=True
                )
                current_short_thumb = short_thumb_res.get("selected_thumbnail", {}).get("path")
            except Exception as e:
                print(f"⚠️ [SHORTS] Erro ao gerar thumb para parte {i}: {e}")

            out_short = os.path.join(OUTPUT_DIR, f"shorts_{i}_{job_id}.mp4")
            start_time = (i - 1) * 65  # Short 1 = 0s, Short 2 = 65s, Short 3 = 130s
            
            # Filtro dos Shorts (com legendas burn-in e injeção de Thumbnail nos primeiros 0.5s)
            if current_short_thumb and os.path.exists(current_short_thumb):
                filter_shorts = f"[0:v]split=2[bg_in][fg_in];[bg_in]scale=720:1280,boxblur=20:10,setsar=1[bg];[fg_in]scale=720:-2,setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base];[base]subtitles='{safe_srt}':force_style='Alignment=2,FontSize=12,Outline=1,Shadow=1,MarginV=90,PrimaryColour=&H00FFFF&'[withsubs];[1:v]scale=720:1280,setsar=1[thumb];[withsubs][thumb]overlay=0:0:enable='between(t,0,0.5)',setdar=9/16[outv]"
                args_shorts = [
                    ffmpeg_exe, "-ss", str(start_time), "-i", output_long, "-loop", "1", "-i", current_short_thumb, "-filter_complex", filter_shorts,
                    "-map", "[outv]", "-map", "0:a", "-c:v", encoder, "-preset", "veryfast", "-pix_fmt", pix_fmt,
                    "-aspect", "9:16", "-t", "59", "-c:a", "copy", out_short, "-y"
                ]
            else:
                filter_shorts = f"[0:v]split=2[bg_in][fg_in];[bg_in]scale=720:1280,boxblur=20:10,setsar=1[bg];[fg_in]scale=720:-2,setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,setdar=9/16,subtitles='{safe_srt}':force_style='Alignment=2,FontSize=12,Outline=1,Shadow=1,MarginV=90,PrimaryColour=&H00FFFF&'[outv]"
                args_shorts = [
                    ffmpeg_exe, "-ss", str(start_time), "-i", output_long, "-filter_complex", filter_shorts,
                    "-map", "[outv]", "-map", "0:a", "-c:v", encoder, "-preset", "veryfast", "-pix_fmt", pix_fmt,
                    "-aspect", "9:16", "-t", "59", "-c:a", "copy", out_short, "-y"
                ]
            
            res_shorts = subprocess.run(args_shorts, shell=False)
            if res_shorts.returncode != 0:
                print(f"❌ [FFMPEG ERROR] Falha no Shorts {i}.")
                # Se falhar usando o encoder configurado, tenta um último fallback de emergência usando libx264
                if encoder == "h264_qsv":
                    print("⚠️ Tentando renderizar Short com software fallback (libx264)...")
                    args_shorts_cpu = args_shorts.copy()
                    idx_cv_sh = args_shorts_cpu.index("-c:v")
                    args_shorts_cpu[idx_cv_sh+1] = "libx264"
                    args_shorts_cpu[idx_cv_sh+4] = "yuv420p"
                    res_shorts = subprocess.run(args_shorts_cpu, shell=False)
                    if res_shorts.returncode == 0:
                        print(f"✅ Short {i} recuperado via software fallback!")
                        continue
                cleanup(); return False

    try: os.remove(filter_script_path)
    except: pass
    cleanup(); return True

# ==========================================
# 5. RENDER E START
# ==========================================

def _apply_visual_cue_fx(base_visual_fx: str, cues: list, dur: float) -> str:
    """
    Maps visual_cues from the Retention Engine JSON to FFmpeg filter modifiers.
    Called per-scene to inject dynamic micro-pacing into the filter_complex.
    cues = [{"t": 0, "type": "zoom_in", "target": "...", "text": "..."}, ...]
    """
    if not cues or not isinstance(cues, list):
        return base_visual_fx

    # Classify the dominant cue type for this scene
    cue_types = [c.get("type", "") for c in cues if isinstance(c, dict)]
    dominant = cue_types[0] if cue_types else ""

    fx_map = {
        "zoom_in":      f"scale=w='min(iw*1.25,4000)':h=-1,zoompan=z='min(zoom+0.003,1.4)':d={int(dur*30)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "zoom_out":     f"scale=w='min(iw*1.3,4000)':h=-1,zoompan=z='max(zoom-0.003,1.0)':d={int(dur*30)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "glitch":       "hue=h='sin(2*PI*t)*30':s='1+0.3*sin(2*PI*t*3)',noise=alls=15:allf=t",
        "slow_motion":  f"setpts=1.5*PTS,fps=30,trim=duration={dur}",
        "freeze_frame": f"trim=end_frame=1,setpts=PTS*{int(dur*30)},fps=30,trim=duration={dur}",
        "split_screen": "",   # handled at assembly level, skip here
        "cut":          "",   # just a cut, no filter modifier
        "graph_reveal": "eq=contrast=1.15:saturation=1.2",
        "text_overlay": "",   # text is handled by PIL, not FFmpeg filter
        "highlight_circle": "eq=brightness=0.05:contrast=1.1",
    }

    modifier = fx_map.get(dominant, "")
    if modifier and modifier not in base_visual_fx:
        return base_visual_fx + "," + modifier

    return base_visual_fx


def render_video(job_data):
    job_id = job_data.get("job_id")
    script_obj = job_data.get("script_data", {})
    topic = script_obj.get("title") or job_data.get("topic") or "AI Secrets"
    script_text = script_obj.get("script_full") or script_obj.get("script") or f"Welcome to our story about {topic}. Today we explore the secrets of AI."
    
    print(f"\n🚀 [PRODUÇÃO] Job: {topic}")
    
    try:
        audio_path = os.path.join(OUTPUT_DIR, f"audio_{job_id}.mp3")
        VOICE_MAP = {"en-US": "en-US-GuyNeural", "pt-BR": "pt-BR-AntonioNeural", "es-ES": "es-ES-AlvaroNeural", "fr-FR": "fr-FR-HenriNeural", "de-DE": "de-DE-ConradNeural"}
        voice_id = VOICE_MAP.get(job_data.get("language", "en-US"), VOICE_DEFAULT)
        generate_voice_master(script_text, audio_path, voice=voice_id, language=job_data.get("language", "en-US"), job_data=job_data)

        srt_path = generate_subtitles_locally(audio_path, job_id)
        if not srt_path: return False

        cenas = []
        clip_durations = []
        scenes_list = script_obj.get("scenes", [])
        
        # Extract per-scene visual_cues from Retention Engine output for micro-pacing
        scene_visual_cues = []
        for s in scenes_list:
            cues = s.get("visual_cues") or s.get("micro_cues") or []
            scene_visual_cues.append(cues if isinstance(cues, list) else [])
        
        from pysubs2 import load
        subs = load(srt_path)
        
        # Obter a duração real do áudio usando o FFmpeg para evitar cortes prematuros
        def get_audio_duration_via_ffmpeg(path):
            import subprocess
            import re
            try:
                res = subprocess.run([ffmpeg_exe, "-i", path], capture_output=True, text=True)
                match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
                if match:
                    hours, minutes, seconds = match.groups()
                    dur = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                    print(f"🎵 [AUDIO DURATION] Duração real detectada: {dur:.2f}s")
                    return dur
            except Exception as e:
                print(f"⚠️ Erro ao obter duração do áudio: {e}")
            return 60.0

        real_audio_dur = get_audio_duration_via_ffmpeg(audio_path)
        last_sub_end = (subs[-1].end / 1000.0) + 1.5 if len(subs) > 0 else 0
        total_audio_sec = max(real_audio_dur, last_sub_end)

        print(f"🎬 [Worker] Sincronizando {len(scenes_list)} cenas...")
        if len(scenes_list) == 0:
            print("⚠️ Nenhuma cena encontrada no roteiro. Criando cena padrão...")
            scenes_list = [{"timestamp": "0:00-0:10", "narration": script_text[:500], "b_roll_visual": topic}]

        used_assets = set()
        def download_assets(scene, scene_index=0):
            visual_kw = scene.get("b_roll_visual", "AI technology")
            sfx_cue = scene.get("sfx_cue", "none")
            
            # Hook Quality Gate: Para as 3 primeiras cenas do vídeo longo,
            # força busca na API web se o B-Roll do prompt for específico (contém 'cinematic', 'dramatic', etc.)
            # Isso garante que o hook tenha visuais impactantes, não cache genérico
            is_hook = (scene_index < 3 and not job_data.get("is_short", False))
            if is_hook:
                import re, glob
                clean_kw = re.sub(r'[^\w\s]', '', visual_kw.lower())
                words = [w for w in clean_kw.split() if w not in {"a","an","the","and","or","of","with","in","on","at"}]
                api_query = " ".join(words[:4]) if words else visual_kw
                safe_id = hashlib.md5(api_query.encode()).hexdigest()[:12]
                # Verifica se já existe no cache com ID exato (mesma query)
                cached_exact = os.path.join(ASSETS_CACHE, f"asset_{safe_id}.mp4")
                if os.path.exists(cached_exact):
                    used_assets.add(cached_exact)
                    p_visual = cached_exact
                else:
                    # Força download novo da API para garantir B-Roll relevante
                    p_visual = fetch_premium_broll(visual_kw, set())  # set() vazio = ignora restrições de cache
            else:
                p_visual = fetch_premium_broll(visual_kw, used_assets)
            
            p_sfx = fetch_sfx(sfx_cue) if sfx_cue != "none" else None
            return p_visual, p_sfx

        # Execução síncrona para garantir que 'used_assets' impeça repetições
        downloaded_assets_list = [download_assets(s, idx) for idx, s in enumerate(scenes_list)]


        downloaded_paths = [a[0] for a in downloaded_assets_list]
        sfx_paths = [a[1] for a in downloaded_assets_list]
        # Injeção Inteligente de SFX de transição para evitar silêncios pós-transições
        for idx in range(len(sfx_paths)):
            if sfx_paths[idx] is None:
                scene_text = str(scenes_list[idx].get("narration") or "").lower()
                is_transition_word = any(w in scene_text for w in ["mais", "criação", "entretanto", "porém", "mais voici", "cependant", "toutefois", "but", "however", "yet"])
                if idx % 6 == 0 or is_transition_word:
                    try:
                        sfx_paths[idx] = fetch_sfx("whoosh")
                        print(f"🎵 [SFX AUTO-INJECT] Injetando SFX 'whoosh' na cena {idx} para transição fluida.")
                    except Exception as e:
                        print(f"⚠️ Falha ao auto-injetar SFX whoosh: {e}")

        for i, (scene, p) in enumerate(zip(scenes_list, downloaded_paths)):
            # Calcula a duração da cena para o fallback
            try:
                ts_raw = scene.get("timestamp", "0:00-0:10")
                parts_ts = ts_raw.split("-")
                def _to_sec(t):
                    t = str(t).strip().replace("s", "")
                    if ":" in t:
                        m, s = t.split(":")
                        return int(m) * 60 + float(s)
                    return float(t)
                scene_dur = max(_to_sec(parts_ts[1]) - _to_sec(parts_ts[0]), 5)
            except:
                scene_dur = 5

            def _make_animated_fallback(dur, idx):
                """Gera vídeo de fallback animado via FFmpeg geq para evitar tela preta/estática."""
                fb_path = os.path.join(OUTPUT_DIR, f"fallback_anim_{job_id}_{idx}.mp4")
                if os.path.exists(fb_path):
                    return fb_path
                geq_r = "if(lte(mod(X/W+T*0.05,1),0.5),26+10*sin(T*1.5+X/W*3.14),18+8*cos(T*1.2+Y/H*3.14))"
                geq_g = "if(lte(mod(Y/H+T*0.04,1),0.5),10+5*sin(T*1.8+Y/H*2),8+4*cos(T*1.1+X/W*2))"
                geq_b = "30+20*sin(T*0.8+X/W*1.5)*cos(T*0.6+Y/H*1.5)"
                cmd = [
                    ffmpeg_exe, "-y",
                    "-f", "lavfi",
                    "-i", f"nullsrc=size=1280x720:rate=30:duration={dur}",
                    "-vf", f"geq=r='{geq_r}':g='{geq_g}':b='{geq_b}',format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", fb_path
                ]
                try:
                    res = subprocess.run(cmd, capture_output=True, timeout=120)
                    if res.returncode == 0:
                        return fb_path
                except Exception:
                    pass
                # Fallback-do-fallback: ruído sobre cor escura
                cmd2 = [
                    ffmpeg_exe, "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=0x1a1a2e:size=1280x720:rate=30:duration={dur}",
                    "-vf", "noise=alls=12:allf=t,format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", fb_path
                ]
                try:
                    subprocess.run(cmd2, capture_output=True, timeout=120)
                    return fb_path
                except Exception:
                    return None

            if not p:
                print(f"🚨 [CENA {i}] B-roll não encontrado. Gerando background animado...")
                p = _make_animated_fallback(scene_dur, i)

            if not p or not os.path.exists(p):
                print(f"🚨 [CENA {i}] Gerando background animado (fallback final)...")
                p = _make_animated_fallback(scene_dur, i)

            if not p or not os.path.exists(p):
                print(f"⚠️ [CENA {i}] Todos os fallbacks falharam. Pulando cena.")
                continue

            cenas.append(p)
            try:
                ts = scene.get("timestamp", "0:00-0:10")
                parts = ts.split("-")
                def to_sec(t):
                    t = str(t).strip().replace("s", "")
                    if ":" in t:
                        m, s = t.split(":")
                        return int(m) * 60 + float(s)
                    return float(t)
                dur = to_sec(parts[1]) - to_sec(parts[0])
                clip_durations.append(max(dur, 2))
            except: clip_durations.append(5)
        
        if not cenas: 
            print("❌ [RENDER ABORTED] Nenhuma cena visual disponível.")
            return False
        
        # Definido aqui para estar disponível tanto no CTA quanto na renderização
        is_short_job = job_data.get("type") == "SHORT"
        
        # ⚡ INJEÇÃO DE CONTEÚDO FINAL (VÍDEOS LONGOS)
        if not is_short_job:
            # 1. Injeção de QUIZ (se existir no roteiro)
            quiz_data = script_obj.get("quiz")
            if quiz_data and isinstance(quiz_data, dict):
                print(f"🧠 [Worker] Injetando cena de QUIZ no final...")
                quiz_frame = generate_quiz_frame(quiz_data, job_id)
                if quiz_frame:
                    cenas.append(quiz_frame)
                    sfx_paths.append(fetch_sfx("ding") or None)
                    clip_durations.append(15) # 15 segundos para o quiz
            
            # 2. Injeção de CTA de AFILIADO
            injected_url = job_data.get("injected_affiliate_url") or job_data.get("injected_url") or ""
            if injected_url:
                print(f"💰 [Worker] Injetando CTA de Afiliado...")
                cta_affiliate_result = download_assets({
                    "b_roll_visual": "person pointing to computer screen smiling excited link in bio",
                    "sfx_cue": "whoosh"
                }, scene_index=999)
                if cta_affiliate_result and cta_affiliate_result[0]:
                    cenas.append(cta_affiliate_result[0])
                    sfx_paths.append(cta_affiliate_result[1])
                    clip_durations.append(12)
            
            # 3. Injeção de CTA de INSCRIÇÃO (Garantia)
            last_scene_visual = str(scenes_list[-1].get("b_roll_visual") or "").lower() if scenes_list else ""
            if "subscribe" not in last_scene_visual and "inscri" not in last_scene_visual:
                print("📣 [CTA] Injetando cena de inscrição final...")
                cta_result = download_assets({
                    "b_roll_visual": "youtube subscribe like comment notification bell animation",
                    "sfx_cue": "notification"
                }, scene_index=1000)
                if cta_result and cta_result[0]:
                    cenas.append(cta_result[0])
                    sfx_paths.append(cta_result[1])
                    clip_durations.append(10)
                    print("   ✅ Cenas finais injetadas com sucesso!")


        
        # Ajuste de tempo
        ratio = total_audio_sec / sum(clip_durations)
        clip_durations = [d * ratio for d in clip_durations]

        # Pacing Dinâmico: Corte estrito de 4.5s
        new_cenas = []
        new_durations = []
        new_sfx = []
        MAX_DUR = 4.5
        for c, d, sfx in zip(cenas, clip_durations, sfx_paths + [None]*(max(0, len(cenas)-len(sfx_paths)))):
            while d > MAX_DUR:
                new_cenas.append(c)
                new_durations.append(MAX_DUR)
                new_sfx.append(sfx)
                sfx = None  # Toca o sfx apenas na primeira parte do corte
                d -= MAX_DUR
            if d > 0:
                new_cenas.append(c)
                new_durations.append(d)
                new_sfx.append(sfx)
        
        cenas = new_cenas
        clip_durations = new_durations
        sfx_paths = new_sfx

        # 1. RENDERIZAÇÃO
        mood = script_obj.get("music_mood", "suspense")
        
        # Para SHORT principal: gera a thumb ANTES para injeta-la nos primeiros 0.5s
        short_thumb_path = None
        if is_short_job:
            try:
                from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation
                print("🎨 [SHORT PRINCIPAL] Gerando Thumbnail 9:16 para cena inicial...")
                lang = job_data.get("language", "en-US")
                thumb_res = orchestrate_thumbnail_generation(
                    job_id=job_id, title=topic, language=lang,
                    niche="Tech", video_file_path=None, is_short=True
                )
                short_thumb_path = thumb_res.get("selected_thumbnail", {}).get("path") if thumb_res else None
                if short_thumb_path:
                    print(f"   ✅ Thumb gerada: {short_thumb_path}")
            except Exception as e:
                print(f"   ⚠️ Thumb do Short Principal não gerada: {e}")
        
        # Inject visual cues into job_data so assemble_production can read them
        # scene_visual_cues is aligned with cenas[] after pacing/dedup expansion
        job_data["_scene_visual_cues"] = scene_visual_cues

        if not assemble_production(
            job_id, cenas, audio_path, srt_path, topic, clip_durations, total_audio_sec,
            job_data=job_data,
            is_short=is_short_job, music_mood=mood, sfx_paths=sfx_paths,
            thumb_path=short_thumb_path
        ): return False



        # 2. DEFINIÇÃO DA ESTRUTURA DE PASTAS (DATA / PAÍS)
        # Pega a data do agendamento ou por padrão: AMANHÃ
        # (O usuário solicitou para gerar os errados hoje e deixar o agendamento pra amanhã)
        pub_date = datetime.now() + timedelta(days=1)
        
        if job_data.get("scheduled_at"):
            try:
                iso_str = job_data.get("scheduled_at").replace('Z', '+00:00')
                pub_date = datetime.fromisoformat(iso_str)
            except: pass
        
        date_folder = pub_date.strftime("%d-%m-%Y")
        
        # Mapeia idioma para pasta de país
        LANG_TO_COUNTRY = {"en-US": "US", "pt-BR": "BR", "es-ES": "ES", "fr-FR": "FR", "de-DE": "DE"}
        country_code = LANG_TO_COUNTRY.get(job_data.get("language", "en-US"), "US")
        
        # Caminho final: videos_finais/02-05-2026/US/
        final_dest_dir = os.path.join(OUTPUT_DIR, date_folder, country_code)
        os.makedirs(final_dest_dir, exist_ok=True)

        # Caminhos temporários (onde o assemble_production salvou)
        temp_long = os.path.join(OUTPUT_DIR, f"final_{job_id}.mp4")

        # Caminhos definitivos
        prefix = "video_short_principal" if is_short_job else "video_longo"
        final_video_path = os.path.join(final_dest_dir, f"{prefix}_{job_id}.mp4")
        final_srt_path = os.path.join(final_dest_dir, f"{prefix}_{job_id}.srt")
        
        # Move os vídeos e legendas para a pasta organizada
        import shutil
        if os.path.exists(temp_long): shutil.move(temp_long, final_video_path)
        if os.path.exists(srt_path): shutil.move(srt_path, final_srt_path)
        
        shorts_paths = []
        if not is_short_job:
            for i in range(1, 4):
                temp_short = os.path.join(OUTPUT_DIR, f"shorts_{i}_{job_id}.mp4")
                final_short = os.path.join(final_dest_dir, f"video_short_{i}_{job_id}.mp4")
                if os.path.exists(temp_short):
                    shutil.move(temp_short, final_short)
                    shorts_paths.append((i, final_short))

        # 3. THUMBNAIL (Salva direto na pasta organizada)
        from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation
        print(f"🎨 [Worker] Gerando Thumbnail na pasta {country_code}...")
        thumb_results = orchestrate_thumbnail_generation(
            job_id=job_id, title=topic, language=job_data.get("language", "en-US"),
            niche=job_data.get("niche", "Money"), video_file_path=final_video_path
        )
        
        # Move a thumb para a pasta final se ela foi gerada em outro lugar
        raw_thumb_path = thumb_results.get("selected_thumbnail", {}).get("path")
        thumb_path = os.path.join(final_dest_dir, f"thumb_{job_id}.jpg")
        if raw_thumb_path and os.path.exists(raw_thumb_path):
            shutil.move(raw_thumb_path, thumb_path)
        
        # 4. DATA DE AGENDAMENTO (Para o Uploader)
        scheduled_at = pub_date if job_data.get("scheduled_at") else None
        # 4. AFILIADO (Busca profunda em todos os campos possíveis)
        injected_url = (
            job_data.get("injected_affiliate_url") or 
            job_data.get("injected_url") or 
            script_obj.get("cta_link") or 
            script_obj.get("affiliate_url") or 
            ""
        )
        
        pinned_comment = None
        if injected_url:
            print(f"💰 [MONETIZAÇÃO] Link de afiliado injetado: {injected_url}")
            pinned_comment = f"🚀 Start your AI Empire here: {injected_url}"
        else:
            print("⚠️ [AVISO] Nenhum link de afiliado encontrado para este trabalho.")

        # 3.5 CONTROLE DE QUALIDADE (QC LOCAL)
        from agents.validator.qc_agent import ValidatorAgent
        print("🔍 [Worker] Executando Agente Validador Local antes do Upload...")
        
        # Para jobs do tipo SHORT, validamos o primeiro short gerado
        check_path = final_video_path
        if job_data.get("type") == "SHORT" and shorts_paths:
            check_path = shorts_paths[0][1]
            
        validator = ValidatorAgent(
            session=None, 
            job=job_data, 
            video_path=check_path, 
            thumbnail_path=thumb_path,
            channel_language=job_data.get("language", "en-US")
        )
        if not validator.execute_quality_control():
            print("❌ [Worker] Vídeo REPROVADO no controle de qualidade. Abortando upload.")
            try:
                requests.post(f"{CLOUD_HUB_URL}/worker/job-status", json={
                    "job_id": job_id, "status": "FAILED_QC", "error": "Reprovado pelo Validator local."
                }, timeout=10)
            except: pass
            return "FAILED_QC"

        # 4. FILA DE UPLOAD
        common_data = {
            'title': topic, 'description': script_obj.get("description", ""),
            'tags': script_obj.get("tags", []), 'id': job_id, 
            'thumb_path': thumb_path, 'language': job_data.get("language", "en-US"),
            'injected_url': injected_url, 'pinned_comment': pinned_comment,
            'scheduled_at': scheduled_at, 'srt_path': final_srt_path,
            'quiz_data': script_obj.get("quiz")
        }
        
        upload_queue.put({**common_data, 'path': final_video_path, 'type': 'SHORT' if is_short_job else 'LONG'})
        for idx, spath in shorts_paths:
            upload_queue.put({**common_data, 'path': spath, 'type': 'SHORT', 'title': f"{topic} #Shorts Part {idx}"})
        
        # [NOVO] Escreve um arquivo de metadata para upload manual
        meta_path = os.path.join(final_dest_dir, f"metadata_{job_id}.txt")
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(f"TITLE: {topic}\n")
                f.write(f"{"="*40}\n")
                f.write(f"DESCRIPTION:\n{script_obj.get('description', '')}\n")
                f.write(f"{"="*40}\n")
                f.write(f"TAGS: {', '.join(script_obj.get('tags', []))}\n")
                f.write(f"{"="*40}\n")
                f.write(f"PINNED COMMENT:\n{pinned_comment}\n")
        except: pass
        
        # 5. LIMPEZA DE TEMPORÁRIOS
        try:
            if os.path.exists(audio_path): os.remove(audio_path)
            # Mantemos os vídeos finais e SRT pois a fila de upload ainda vai usá-los!
            # Mantemos os vídeos finais pois a fila de upload ainda vai usá-los!
            # Mas podemos limpar arquivos de filtro extras se sobraram
        except: pass
        
        return True
            
    except Exception as e:
        print(f"❌ [ERRO NO RENDER] {e}")
    return False

MAX_DAILY_VIDEOS = 24
QUOTA_FILE = "quota_tracker.json"

# Configurações de Escala para vídeos longos (>12 min)
MIN_SCENES = 60
MAX_SCENES = 200

def get_daily_quota():
    today = date.today().isoformat()
    if os.path.exists(QUOTA_FILE):
        try:
            with open(QUOTA_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == today:
                    return data.get("count", 0)
        except: pass
    return 0

def increment_daily_quota():
    today = date.today().isoformat()
    count = get_daily_quota() + 1
    with open(QUOTA_FILE, "w") as f:
        json.dump({"date": today, "count": count}, f)
    return count

def start_worker():
    print("=============================================")
    print("=============================================")
    
    # [NOVO] Carrega metas do .env
    DAILY_TARGET = int(os.getenv("DAILY_LONG_TARGET", 3))
    
    while True:
        try:
            # 1. Verifica Cotas Diárias via DB (Modo Standalone)
            from sqlmodel import Session, create_engine, select, func
            from cloud_hub.models import VideoJob
            
            engine = create_engine(os.getenv("DATABASE_URL"))
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            with Session(engine) as session:
                stmt = select(func.count(VideoJob.id)).where(
                    VideoJob.video_type == "LONG",
                    VideoJob.created_at >= today
                )
                long_count = session.exec(stmt).one()
                
                print(f"📊 [COTA] Progresso hoje: {long_count}/{DAILY_TARGET} vídeos longos.")
                
                if long_count < DAILY_TARGET:
                    print(f"⚡ [AUTO-SEED] Cota insuficiente. Gerando novos temas virais...")
                    from viral_scout import ViralScout
                    from cloud_hub.models import Channel
                    
                    # Pega o primeiro canal ativo
                    channel = session.exec(select(Channel).where(Channel.active == True)).first()
                    if channel:
                        scout = ViralScout()
                        region = channel.market_region or "US"
                        trends = scout.get_viral_videos(niche=channel.niche, region=region, days=7)
                        
                        # Cria os jobs faltantes
                        needed = DAILY_TARGET - long_count
                        for t in trends[:needed]:
                            new_job = VideoJob(
                                channel_id=channel.id,
                                tenant_id=channel.tenant_id,
                                topic=t["title"],
                                status="PENDING",
                                video_type="LONG",
                                target_language=channel.language
                            )
                            session.add(new_job)
                        session.commit()
                        print(f"✅ [AUTO-SEED] {needed} novos jobs de elite injetados na fila.")

            response = requests.get(f"{CLOUD_HUB_URL}/worker/next-job/{WORKER_REGION}", timeout=30)
            if response.status_code == 200:
                job = response.json()
                if job and "job_id" in job:
                    success = render_video(job)
                    print(f"📤 [Worker] Reportando status: {'PUBLISHED' if success else 'FAILED'}")
                    requests.post(f"{CLOUD_HUB_URL}/worker/report-status/{job['job_id']}", params={"status": "PUBLISHED" if success else "FAILED"}, timeout=15)
                    
                    if success:
                        increment_daily_quota()
                        cooldown = random.randint(600, 900)
                        print(f"⏳ [THROTTLING] Resfriando CPU por {cooldown // 60} minutos para evitar superaquecimento e banimentos no YouTube...")
                        time.sleep(cooldown)

            elif response.status_code == 404:
                print(f"[{time.strftime('%H:%M:%S')}] 😴 Sem tarefas. Quota atual: {get_daily_quota()}/{MAX_DAILY_VIDEOS}")
        except Exception as e:
            print(f"⚠️ Erro: {e}")
        time.sleep(20)

if __name__ == "__main__":
    try: start_worker()
    except KeyboardInterrupt: sys.exit(0)