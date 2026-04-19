import os
from uuid import UUID, uuid4
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from sqlmodel import Session, create_engine, select
from dotenv import load_dotenv

from cloud_hub.models import Tenant, Channel, VideoJob, AffiliateProduct, PerformanceLog

load_dotenv()

# Configurações do Banco Neon
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

app = FastAPI(title="Dark Channel Factory Cloud-Hub API", version="2.1")

# Dependência do Banco
def get_session():
    with Session(engine) as session:
        yield session


# =============================================================================
# 1. GESTÃO DE TENANTS & ADMIN
# =============================================================================

@app.post("/admin/init-super-admin", tags=["Admin"])
def init_super_admin(session: Session = Depends(get_session)):
    """Cria o Super Admin (Derik) se não existir."""
    existing = session.exec(select(Tenant).where(Tenant.email == "derikmoretti@gmail.com")).first()
    if existing:
        return {"msg": "Super Admin already exists", "tenant_id": existing.id}
    
    super_admin = Tenant(
        name="Derik Moretti (Super Admin)",
        email="derikmoretti@gmail.com",
        plan_type="enterprise",
        credits_balance=999999,
        auto_approve_content=True
    )
    session.add(super_admin)
    session.commit()
    session.refresh(super_admin)
    return {"msg": "Super Admin created", "tenant_id": super_admin.id}


@app.get("/admin/all-jobs", tags=["Admin"])
def get_all_jobs(session: Session = Depends(get_session)):
    """Acesso total: Super Admin visualiza TODOS os jobs de todos os tenants."""
    # Nota: Em prod, aqui haveria um check de Auth Token do Admin
    jobs = session.exec(select(VideoJob)).all()
    return jobs


# =============================================================================
# 2. SMART MONETIZATION & ANALYST WEBHOOKS
# =============================================================================

@app.post("/webhooks/hotmart", tags=["Finance"])
async def hotmart_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """
    Webhook da Hotmart para capturar vendas.
    Espera o parâmetro 'src' no formato [CANAL_ID]-[VIDEO_ID]
    """
    data = await request.json()
    # Hotmart envia SRC no campo 'src' ou 'hsrc'
    src = data.get("src") or data.get("hsrc")
    
    if src and "-" in src:
        try:
            _, video_id_str = src.split("-")
            video_uuid = UUID(video_id_str)
            commission_value = float(data.get("commission", {}).get("value", 0))
            
            # Registrar Venda no Performance Log
            background_tasks.add_task(update_roi, video_uuid, commission_value, session)
            return {"status": "assigned", "video_id": video_id_str}
        except:
            pass

    return {"status": "received", "msg": "No tracking ID found"}


@app.post("/webhooks/clickbank", tags=["Finance"])
async def clickbank_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """
    Webhook do ClickBank INS (Instant Notification Service).
    Espera 'tracking_id' no formato [CANAL_ID]-[VIDEO_ID]
    """
    data = await request.json()
    tid = data.get("trackingId")
    
    if tid and "-" in tid:
        try:
            _, video_id_str = tid.split("-")
            video_uuid = UUID(video_id_str)
            # ClickBank envia valor bruto ou comissão? (Depende do payload)
            revenue = float(data.get("payoutAmount", 0))
            
            background_tasks.add_task(update_roi, video_uuid, revenue, session)
            return {"status": "assigned", "video_id": video_id_str}
        except:
            pass
            
    return {"status": "received"}


def update_roi(video_uuid: UUID, value: float, session: Session):
    """Lógica do Analyst Agent para atualizar o banco de ROI."""
    # Busca log existente ou cria um novo
    job = session.get(VideoJob, video_uuid)
    if not job:
        return

    log = session.exec(select(PerformanceLog).where(PerformanceLog.video_id == video_uuid)).first()
    if not log:
        log = PerformanceLog(video_id=video_uuid, tenant_id=job.tenant_id)
        session.add(log)
    
    log.sales_count += 1
    log.affiliate_revenue += value
    log.last_updated = datetime.now(timezone.utc)
    
    session.commit()


# =============================================================================
# 3. PIPELINE DE INTELIGÊNCIA (CLOUD)
# =============================================================================

from cloud_hub.core.intelligence import run_scout_phase, run_viral_score_phase
from cloud_hub.core.copywriter import run_copywriter_phase

@app.post("/admin/trigger-scout/{channel_id}", tags=["Intelligence"])
async def trigger_scout(channel_id: UUID, session: Session = Depends(get_session)):
    """Inicia o processo: Acha tópico -> Avalia Viralidade -> Escreve Roteiro."""
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
        
    # 1. Scout
    job = await run_scout_phase(session, channel)
    
    # 2. Viral Score
    score = await run_viral_score_phase(session, job)
    
    if score < 70:
        return {"status": "aborted", "reason": "Low viral score", "score": score}
        
    # 3. Copywriter & Monetization
    await run_copywriter_phase(session, job)
    
    return {
        "status": "success", 
        "job_id": job.id, 
        "topic": job.topic, 
        "affiliate": job.injected_affiliate_url
    }


# =============================================================================
# 4. INTERFACE COM O WORKER LOCAL
# =============================================================================

@app.get("/worker/next-job/{market_region}", tags=["Worker"])
def get_next_job(market_region: str, session: Session = Depends(get_session)):
    """O Worker Local chama este endpoint para saber o que processar."""
    # Busca jobs que já foram APROVADOS (IA completa)
    statement = select(VideoJob).where(
        VideoJob.status == "APPROVED"
    ).join(Channel).where(Channel.market_region == market_region).limit(1)
    
    job = session.exec(statement).first()
    if not job:
        # Se não houver aprovado, tenta um DRAFTED se o canal for do Super Admin
        statement = select(VideoJob).where(
            VideoJob.status == "DRAFTED"
        ).join(Tenant).where(Tenant.email == "derikmoretti@gmail.com").limit(1)
        job = session.exec(statement).first()

    if not job:
        return {"status": "idle", "msg": "No jobs ready for rendering"}
    
    return {
        "job_id": job.id,
        "niche": job.channel.niche,
        "language": job.channel.language,
        "script_data": job.script_data,
        "thumbnail_data": job.thumbnail_data,
        "injected_url": job.injected_affiliate_url
    }

@app.post("/worker/report-status/{job_id}", tags=["Worker"])
def report_status(job_id: UUID, status: str, error: Optional[str] = None, session: Session = Depends(get_session)):
    """O Worker Local avisa se o vídeo foi renderizado ou se deu erro."""
    job = session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.status = status
    if error:
        job.error_message = error
    job.updated_at = datetime.now(timezone.utc)
    
    session.commit()
    return {"status": "ok"}
