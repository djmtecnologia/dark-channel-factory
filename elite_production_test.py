import os
os.environ["DISABLE_XTTS"] = "1"
import sys
import time
import asyncio
import json
import warnings
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from sqlmodel import Session, create_engine, select, SQLModel
from cloud_hub.models import Channel, VideoJob, ViralTheme, Tenant
from cloud_hub.core.copywriter import run_copywriter_phase
from viral_scout import ViralScout
from dotenv import load_dotenv
import desktop_node  # Importado no topo para acesso global (upload_queue)

# 1. SUPRESSÃO DE AVISOS (Limpeza de Log)
try:
    from requests.packages.urllib3.exceptions import DependencyWarning
    warnings.filterwarnings("ignore", category=DependencyWarning)
except: pass

load_dotenv()

# 2. CONFIGURAÇÃO DE BANCO DE DADOS (Resiliência)
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True,
    pool_recycle=300
)

async def create_real_viral_jobs(channel, count):
    """Minera tendências reais de 2026 com diversificação e cria a fila."""
    region = channel.market_region or "US"
    print(f"🔎 [SCOUT] Minerando {count} tendências para o Canal {channel.identifier} (Região: {region})...")
    scout = ViralScout()
    # niche=None ativa a rotação aleatória de temas (IA, Financial, Web3)
    trends = scout.get_viral_videos(niche=None, region=region, days=7)
    
    if not trends:
        print(f"⚠️ Nenhuma tendência fresca para {region}. Usando temas da Blueprint 2026.")
        topics = [
            "AI System Architecture: Scalability secrets for 2026",
            "Financial Engineering with Autonomous Agents"
        ][:count]
    else:
        topics = [t["title"] for t in trends[:count]]

    job_ids = []
    with Session(engine) as session:
        for topic in topics:
            new_job = VideoJob(
                channel_id=channel.id,
                tenant_id=channel.tenant_id,
                topic=topic,
                status="PENDING",
                video_type="LONG",
                target_language=channel.language,
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2)
            )
            session.add(new_job)
            session.commit()
            session.refresh(new_job)
            job_ids.append(new_job.id)
            print(f"✅ Job Criado: {new_job.id} | Tópico: {topic} ({channel.identifier})")
            
    return job_ids

async def setup_elite_staging(force_quota=False, target_langs=""):
    print("====================================================")
    print("🛡️  DARK CHANNEL FACTORY - MODO MULTI-CANAL ELITE")
    if force_quota:
        print("🔥 [FORCE] Ignorando cotas diárias. Produção total ativa!")
    print("====================================================")
    
    all_job_ids = []
    
    with Session(engine) as session:
        # ... (rest of the setup logic)
        tenant = session.exec(select(Tenant).where(Tenant.name == "Elite Production")).first()
        if not tenant:
            tenant = Tenant(name="Elite Production", email="elite@factory.ia", plan_type="enterprise")
            session.add(tenant)
            session.commit()
            session.refresh(tenant)

        channel_configs = [
            {"name": "AI Money US", "id": "en-US", "region": "US", "lang": "en-US", "voice": "en-US-AriaNeural"},
            {"name": "IA Argent France", "id": "fr-FR", "region": "FR", "lang": "fr-FR", "voice": "fr-FR-DeniseNeural"},
            {"name": "IA Dinero España", "id": "es-ES", "region": "ES", "lang": "es-ES", "voice": "es-ES-ElviraNeural"},
            {"name": "KI Geld Deutschland", "id": "de-DE", "region": "DE", "lang": "de-DE", "voice": "de-DE-KatjaNeural"}
        ]

        if target_langs:
            langs_list = [l.strip() for l in target_langs.split(',')]
            channel_configs = [c for c in channel_configs if c["id"] in langs_list]
            print(f"🎯 [Filtro] Produzindo apenas para: {langs_list}")

        for config in channel_configs:
            channel = session.exec(select(Channel).where(Channel.identifier == config["id"])).first()
            if not channel:
                channel = Channel(
                    name=config["name"],
                    identifier=config["id"],
                    market_region=config["region"],
                    language=config["lang"],
                    voice=config["voice"],
                    niche="Money",
                    active=True,
                    tenant_id=tenant.id
                )
                session.add(channel)
                session.commit()
                session.refresh(channel)

            DAILY_TARGET = int(os.getenv("DAILY_LONG_TARGET", 1))
            from sqlalchemy import func, and_
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            stmt = select(func.count(VideoJob.id)).where(
                and_(
                    VideoJob.channel_id == channel.id,
                    VideoJob.video_type == "LONG",
                    VideoJob.created_at >= today
                )
            )
            long_count = session.exec(stmt).one()
            
            print(f"📊 [COTA] {channel.identifier}: {long_count}/{DAILY_TARGET} hoje.")
            
            if force_quota or long_count < DAILY_TARGET:
                needed = DAILY_TARGET if force_quota else (DAILY_TARGET - long_count)
                jobs = await create_real_viral_jobs(channel, needed)
                all_job_ids.extend(jobs)
            else:
                print(f"✅ Meta diária atingida para {channel.identifier}. Pulando.")

    return all_job_ids


async def run_elite_pipeline(job_id):
    print(f"\n🌀 [ELITE PIPELINE] Processando: {job_id}")
    
    with Session(engine) as session:
        job = session.get(VideoJob, job_id)
        if not job: return

        print("✍️ [IA] Gerando Roteiro (Contexto 2026 + 80/20 Retention)...")
        try:
            # A função run_copywriter_phase já usa o novo prompt agressivo que editamos
            await run_copywriter_phase(session, job)
            print(f"✅ Roteiro gerado para: {job.topic[:50]}...")
            
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
        except Exception as e:
            print(f"❌ Erro no Copywriter: {e}")
            return

    # FASE DE RENDERIZAÇÃO
    print("🎬 [RENDER] Iniciando Motor de Vídeo (High Pacing Mode)...")
    try:
        # O render_video agora tem o QC Agent fixado internamente para não dar "unknown"
        success = desktop_node.render_video(job_data)
        
        if success:
            print(f"🚀 [SUCESSO] Vídeo gerado e validado pelo QC!")
        else:
            print(f"❌ [FALHA] Renderização ou QC falhou.")
            
    except Exception as e:
        print(f"❌ [ERRO CRÍTICO] {e}")

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Força geração mesmo se cota atingida")
    parser.add_argument("--langs", type=str, default="", help="Filtra por idiomas especificos (ex: es-ES,de-DE)")
    args = parser.parse_args()

    job_ids = await setup_elite_staging(force_quota=args.force, target_langs=args.langs)
    
    for jid in job_ids:
        await run_elite_pipeline(jid)
    
    print("\n🎉 [ELITE TEST] Ciclo completo finalizado!")
    print("📁 Pastas 'videos_finais' e 'assets_cache' atualizadas.")
    
    # [NOVO] Aguarda a finalização de todos os uploads em background
    if not desktop_node.upload_queue.empty():
        print("⏳ [UPLOAD] Aguardando a finalização dos uploads na fila...")
        desktop_node.upload_queue.join()
        print("✅ [UPLOAD] Todos os vídeos foram publicados com sucesso!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Teste interrompido pelo usuário.")
        sys.exit(0)
