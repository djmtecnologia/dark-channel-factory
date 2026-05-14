import os
import time
import asyncio
import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from sqlmodel import Session, create_engine, select
from cloud_hub.models import Channel, VideoJob, ViralTheme, Tenant
from cloud_hub.core.copywriter import run_copywriter_phase
from dotenv import load_dotenv

load_dotenv()

# Configuração do Banco de Dados com Resiliência (Evita OperationalError e SSL drops)
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True,  # Verifica se a conexão está viva antes de usar
    pool_recycle=300,    # Reinicia conexões a cada 5 minutos
    pool_size=10,        # Pool de conexões
    max_overflow=20      # Permite overflow temporário
)

async def setup_staging():
    print("🚀 [STAGING] Iniciando setup de teste completo...")
    
    with Session(engine) as session:
        # 1. Garantir que o Tenant existe
        staging_tenant = session.exec(select(Tenant).where(Tenant.name == "Staging Team")).first()
        if not staging_tenant:
            print("🏢 Criando Tenant de teste...")
            staging_tenant = Tenant(
                name="Staging Team",
                email="staging@example.com",
                plan_type="enterprise"
            )
            session.add(staging_tenant)
            session.commit()
            session.refresh(staging_tenant)

        # 2. Garantir que o canal US existe
        us_channel = session.exec(select(Channel).where(Channel.market_region == "US")).first()
        if not us_channel:
            print("📺 Criando canal US de teste...")
            us_channel = Channel(
                name="AI Tools US (Staging)",
                market_region="US",
                language="en-US",
                voice="en-US-AriaNeural",
                niche="AI Tools",
                active=True,
                tenant_id=staging_tenant.id
            )
            session.add(us_channel)
            session.commit()
            session.refresh(us_channel)
        
        return await create_test_jobs()
async def create_test_jobs():
    """Cria jobs de teste multi-idioma."""
    with Session(engine) as session:
        # Pega ou cria o canal
        us_channel = session.exec(select(Channel)).first()
        
        jobs = [
            ("LONG", "How AI is Replacing Software Engineers in 2026", "en-US"),
            ("SHORT", "AI Apocalypse 2026", "en-US")
        ]
        
        job_ids = []
        for v_type, topic, lang in jobs:
            new_job = VideoJob(
                channel_id=us_channel.id if us_channel else "default",
                topic=topic,
                status="PENDING",
                video_type=v_type,
                target_language=lang,
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2)
            )
            session.add(new_job)
            session.commit()
            session.refresh(new_job)
            job_ids.append(new_job.id)
            
        print(f"✅ {len(job_ids)} Jobs multi-idioma criados!")
        return job_ids

async def run_pipeline_for_job(job_id):
    print(f"\n🌀 [PIPELINE] Processando Job: {job_id}")
    
    # 1. Recupera o job e executa o copywriter
    with Session(engine) as session:
        job = session.get(VideoJob, job_id)
        if not job:
            print("❌ Job não encontrado!")
            return
        
        # --- FASE 1: COPYWRITER ---
        print("✍️ Executando Copywriter...")
        try:
            await run_copywriter_phase(session, job)
            print("✅ Roteiro gerado com sucesso!")
            
            # Prepara os dados para o Editor antes de fechar a sessão
            job_data = {
                "job_id": str(job.id),
                "topic": job.topic,
                "language": job.script_data.get("language", "en-US") if job.script_data else "en-US", 
                "script_data": job.script_data,
                "injected_url": job.injected_affiliate_url,
                "niche": "AI Business",
                "type": job.video_type,
                "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None
            }
        except Exception as e:
            print(f"❌ Erro no Copywriter: {e}")
            return

    # --- FASE 2: VIDEO EDITOR (PRODUCTION MODE) ---
    # FEITO FORA DA SESSÃO DO BANCO DE DADOS PARA EVITAR TIMEOUT
    print("🎬 Executando Video Editor Especialista (Production Worker Logic)...")
    
    try:
        import desktop_node
        
        # Executa a renderização real (com zoom, áudio mix e cache local)
        success = desktop_node.render_video(job_data)
        
        if success == True:
            # O próprio desktop_node já fez a validação e jogou na fila!
            print(f"✅ Vídeo aprovado e colocado na fila de upload!")
            with Session(engine) as session:
                job = session.get(VideoJob, job_id)
                job.status = "READY_FOR_UPLOAD"
                session.add(job)
                session.commit()
        elif success == "FAILED_QC":
            print("❌ [STAGING] Vídeo reprovado no controle de qualidade interno. Marcado para refação.")
            with Session(engine) as session:
                job = session.get(VideoJob, job_id)
                job.status = "FAILED_QC"
                session.add(job)
                session.commit()
        else:
            print(f"❌ Falha na renderização do vídeo.")
            
    except Exception as e:
        print(f"❌ Erro no Video Editor: {e}")
        # Tenta atualizar o status para FAILED caso o banco ainda responda (com retry de conexão)
        try:
            import time
            time.sleep(2)
            with Session(engine) as session:
                job = session.get(VideoJob, job_id)
                if job:
                    job.status = "FAILED"
                    job.error_message = str(e)
                    session.commit()
        except Exception as db_err:
            print(f"⚠️ Falha crítica ao atualizar banco: {db_err}")

async def main():
    job_ids = await setup_staging()
    
    # Processa todos os jobs criados (Multi-idioma)
    for job_id in job_ids:
        await run_pipeline_for_job(job_id)
    
    print("\n⏳ [STAGING] Aguardando Uploader finalizar a fila de envio no YouTube...")
    try:
        import desktop_node
        desktop_node.upload_queue.join()
    except Exception as e:
        print(f"⚠️ Erro ao aguardar fila: {e}")
        
    print("\n🎉 [STAGING] Teste concluído!")
    print("📁 Verifique a pasta 'videos_finais' para os vídeos gerados.")
    print("📁 Verifique a pasta 'assets_cache' para os assets reutilizados.")

if __name__ == "__main__":
    asyncio.run(main())
