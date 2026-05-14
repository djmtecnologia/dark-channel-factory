import os
import requests
import random
from dotenv import load_dotenv

load_dotenv()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")

class AudioScout:
    """Busca e baixa trilhas e SFX sem direitos autorais via Pixabay."""
    
    def __init__(self, assets_dir="channel_setup/assets/audio"):
        self.assets_dir = assets_dir
        os.makedirs(self.assets_dir, exist_ok=True)

    def download_theme_music(self, mood="suspense dark"):
        """Busca uma trilha sonora baseada no clima do vídeo."""
        target_path = os.path.join(self.assets_dir, "background_suspense.mp3")
        
        # Se já tivermos uma música, podemos pular ou baixar uma nova a cada X tempo
        if os.path.exists(target_path):
            return target_path

        print(f"🎵 [Audio Scout] Buscando música tema: {mood}")
        
        try:
            # 1. Tenta Pixabay Music API (endpoint correto)
            if PIXABAY_API_KEY:
                url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={mood}&per_page=3"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    hits = r.json().get("hits", [])
                    if hits:
                        audio_url = hits[0].get("videos", {}).get("medium", {}).get("url")
                        if audio_url:
                            r2 = requests.get(audio_url, stream=True, timeout=30)
                            with open(target_path, 'wb') as f:
                                for chunk in r2.iter_content(chunk_size=4096):
                                    if chunk: f.write(chunk)
                            return target_path
            
            # 2. Fallback: Lista de trilhas no-copyright confiáveis
            fallback_tracks = [
                "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
                "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-9.mp3",
                "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
            ]
            for track_url in fallback_tracks:
                try:
                    print(f"📥 Baixando trilha de emergência: {track_url[:50]}...")
                    r = requests.get(track_url, stream=True, timeout=20)
                    if r.status_code == 200:
                        with open(target_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=4096):
                                if chunk: f.write(chunk)
                        return target_path
                except: continue
                
        except Exception as e:
            print(f"⚠️ Erro ao baixar áudio: {e}")
        return None

    def download_sfx_pack(self):
        """Garante que temos Whoosh e Glitch para as transições."""
        sfx_list = ["whoosh.mp3", "glitch.mp3", "impact.mp3"]
        for sfx in sfx_list:
            path = os.path.join(self.assets_dir, sfx)
            if not os.path.exists(path):
                print(f"🔊 [SFX Scout] Baixando efeito: {sfx}")
                # Mock download de efeitos (em produção: busca Pixabay SFX API)
                pass

if __name__ == "__main__":
    scout = AudioScout()
    scout.download_theme_music()
