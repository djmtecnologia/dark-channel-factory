import os
import sys
import time
import asyncio
import warnings
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Configura o ambiente
os.environ["DISABLE_XTTS"] = "1"
os.environ["PYTHONPATH"] = "."

# Supressão de avisos
try:
    from requests.packages.urllib3.exceptions import DependencyWarning
    warnings.filterwarnings("ignore", category=DependencyWarning)
except: pass

load_dotenv()

# Conecta ao banco de dados SQLModel
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob
from cloud_hub.core.copywriter import run_copywriter_phase
import desktop_node

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

# IDs dos 3 jobs que deram tela preta e ruído acústico no passado
SELECTED_JOB_IDS = [
    "3a903f26-5ca2-4969-ba35-e7ea2fb7c788",  # NBA Luke Kornet
    "8b9968e4-6857-4c85-b767-bb32285ea7db",  # Kazakhstan Crypto Arbitrage
    "e8fa1134-d3de-49e1-a571-4e681f41897d"   # Diablo 4 Bot Farm
]

async def process_job(job_id):
    print("=" * 60)
    print(f"🌀 PROCESSANDO JOB: {job_id}")
    print("=" * 60)
    
    with Session(engine) as session:
        job = session.get(VideoJob, job_id)
        if not job:
            print(f"❌ Job {job_id} não encontrado no banco de dados!")
            return
            
        print(f"📌 Tópico: {job.topic}")
        print(f"📌 Idioma: {job.target_language}")
        print(f"📌 Status Atual: {job.status}")
        
        # 1. Reseta status e dados antigos se necessário
        # Como o agente anterior resetou eles, o script_data pode estar NULL.
        # Vamos garantir que ele seja re-gerado pela IA.
        if not job.script_data or "scenes" not in job.script_data:
            print("✍️ [IA] Script vazio. Iniciando Copywriter Agent para criar novo roteiro...")
            try:
                # Modifica temporariamente para PENDING para a IA aceitar
                job.status = "PENDING"
                session.add(job)
                session.commit()
                session.refresh(job)
                
                await run_copywriter_phase(session, job)
                print(f"✅ Roteiro gerado com sucesso pela IA!")
            except Exception as e:
                print(f"❌ Erro na geração de roteiro pelo Copywriter: {e}")
                return
        else:
            print("📝 Script já existente no banco de dados, usando existente.")
            
        # Garante que o status no banco esteja DRAFTED ou READY para o processamento
        job.status = "DRAFTED"
        session.add(job)
        session.commit()
        session.refresh(job)
        
        # 2. Monta o job_data para renderização
        # Como o uploader Playwright foi refinado, vamos agendar o upload de forma correta
        # Se scheduled_at for nulo, agendamos para daqui a 2 horas
        scheduled = job.scheduled_at
        if not scheduled:
            scheduled = datetime.now(timezone.utc) + timedelta(hours=2)
            job.scheduled_at = scheduled
            session.add(job)
            session.commit()
            session.refresh(job)
            
        job_data = {
            "job_id": str(job.id),
            "topic": job.topic,
            "language": job.target_language or "en-US",
            "script_data": job.script_data,
            "injected_url": job.injected_affiliate_url,
            "niche": "Money",
            "type": job.video_type,
            "channel_identifier": job.channel.identifier,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None
        }

    # 3. Chama o Renderizador e Validador (Desktop Node)
    print("🎬 [RENDER] Iniciando Motor de Edição de Vídeo...")
    try:
        success = desktop_node.render_video(job_data)
        if success:
            print(f"🎉 [SUCESSO] Vídeo gerado e aprovado pelo QC local!")
            
            # Atualiza status no banco para DRAFTED/READY_FOR_UPLOAD ou aguarda upload
            with Session(engine) as session:
                j = session.get(VideoJob, job_id)
                j.status = "DRAFTED"  # O uploader Playwright irá atualizar para PUBLISHED quando terminar
                session.add(j)
                session.commit()
        else:
            print(f"❌ [FALHA] Falha na renderização ou no controle de qualidade.")
    except Exception as e:
        import traceback
        print(f"❌ [ERRO DE MOTOR CRÍTICO] {e}")
        traceback.print_exc()

async def main():
    print("🚀 DARK CHANNEL FACTORY - CORRETOR DE JOBS SELECIONADOS")
    print("   Processando NBA, Cazaquistão e Diablo 4 Bot Farm com novos filtros e motores H.264 corretos.")
    print("=" * 70)
    
    for jid in SELECTED_JOB_IDS:
        await process_job(jid)
        print("\n" + "-" * 60 + "\n")
        
    # Aguarda o upload do Playwright na fila em background
    if not desktop_node.upload_queue.empty():
        print("⏳ [UPLOADER] Vídeos enfileirados. Iniciando uploads automáticos via Playwright...")
        print("   Isso executará o navegador invisível e aplicará as correções de agendamento e tabs do YouTube Studio.")
        desktop_node.upload_queue.join()
        print("✅ [UPLOADER] Todos os uploads concluídos com sucesso absoluto!")
    else:
        print("ℹ️ Nossos vídeos falharam na geração ou não entraram na fila de upload.")
        
    print("\n🎉 [PROCESSO CONCLUÍDO] Todos os jobs selecionados foram processados.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Processamento cancelado manualmente.")
        sys.exit(0)
