import os
import time
import requests
import asyncio
from typing import Optional
from dotenv import load_dotenv

# Importar lógica local de renderização e upload
from desktop_worker.renderer.video_engine import render_video
from desktop_worker.uploader.youtube_manager import upload_to_youtube
from channel_setup.chrome_profile_mapper import get_chrome_profiles

load_dotenv()

# Configurações do SaaS
CLOUD_HUB_URL = os.getenv("CLOUD_HUB_URL", "https://your-app.vercel.app")
WORKER_ID = os.getenv("COMPUTERNAME", "local-worker-01")
MARKET_REGION = os.getenv("MARKET_REGION", "US")  # US, ES, DE, FR

def fetch_next_job():
    """Busca o próximo trabalho pendente no Cloud Hub."""
    try:
        response = requests.get(f"{CLOUD_HUB_URL}/worker/next-job/{MARKET_REGION}")
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "idle":
                return None
            return data
    except Exception as e:
        print(f"❌ Erro ao conectar no Cloud Hub: {e}")
    return None

def report_status(job_id: str, status: str, error: Optional[str] = None):
    """Informa ao Cloud Hub o progresso do trabalho."""
    try:
        requests.post(
            f"{CLOUD_HUB_URL}/worker/report-status/{job_id}",
            params={"status": status, "error": error}
        )
    except Exception as e:
        print(f"⚠️ Erro ao reportar status: {e}")

async def get_my_chrome_profile() -> str:
    """Busca o caminho do perfil Chrome para o worker atual."""
    profiles = get_chrome_profiles()
    # Pega o primeiro perfil para testes, 
    # depois podemos mapear por canal_id vindo do job
    if profiles:
        return profiles[0]["path"]
    return ""

async def process_loop():
    """Loop infinito de processamento local."""
    print(f"🚀 Worker {WORKER_ID} iniciado para a região {MARKET_REGION}")
    print(f"🔗 Conectado em: {CLOUD_HUB_URL}")
    
    profile_path = await get_my_chrome_profile()
    
    while True:
        job = fetch_next_job()
        
        if job:
            job_id = job["job_id"]
            print(f"📦 Novo Job recebido: {job_id} | Nicho: {job['niche']}")
            
            try:
                # 1. Fase de Renderização
                report_status(job_id, "RENDERING")
                video_path = await render_video(job)
                
                # 2. Fase de Upload
                report_status(job_id, "UPLOADING")
                
                metadata = {
                    "title": job["script_data"].get("title", "Modern Tech Trends"),
                    "description": job["script_data"].get("script_full", "")[:1000] + "\n\n#AI #Technology"
                }
                
                await upload_to_youtube(
                    video_path=video_path,
                    metadata=metadata,
                    profile_path=profile_path
                )
                
                # 3. Finaliza com Sucesso
                report_status(job_id, "PUBLISHED")
                print(f"✅ Job {job_id} concluído e publicado!")
                
            except Exception as e:
                print(f"❌ Falha no Job {job_id}: {e}")
                report_status(job_id, "FAILED", error=str(e))
        
        else:
            print("💤 Aguardando novos jobs...")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(process_loop())
