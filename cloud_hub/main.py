import os
from uuid import UUID, uuid4
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from sqlmodel import Session, create_engine, select
from dotenv import load_dotenv

from cloud_hub.models import Tenant, Channel, VideoJob, AffiliateProduct, PerformanceLog

load_dotenv()

# Configurações do Banco Neon
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})

app = FastAPI(title="Dark Channel Factory Cloud-Hub API", version="2.1")

# Finite State Machine - Transições válidas de estado
VALID_TRANSITIONS = {
    "PENDING": ["SCOUTED", "FAILED"],
    "SCOUTED": ["DRAFTED", "FAILED"],
    "DRAFTED": ["APPROVED", "FAILED"],
    "APPROVED": ["PROCESSING", "FAILED"],
    "PROCESSING": ["RENDERING", "FAILED"],
    "RENDERING": ["UPLOADING", "FAILED"],
    "UPLOADING": ["PUBLISHED", "FAILED"],
    "PUBLISHED": [],
    "FAILED": ["PROCESSING"]  # Permite retry
}

# Configuração de timeout de jobs (em minutos)
JOB_TIMEOUT_MINUTES = 30

import asyncio

async def job_timeout_watchdog():
    """
    Monitora jobs que ficaram presos em estados de processamento.
    Roda em background e reseta jobs que excederam o timeout.
    """
    while True:
        try:
            with Session(engine) as session:
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=JOB_TIMEOUT_MINUTES)
                
                # Jobs em processamento há mais de JOB_TIMEOUT_MINUTES
                stale_jobs = session.exec(
                    select(VideoJob)
                    .where(VideoJob.status.in_(["PROCESSING", "RENDERING", "UPLOADING"]))
                    .where(VideoJob.updated_at < cutoff)
                ).all()
                
                for job in stale_jobs:
                    print(f"⚠️ Job {job.id} timeout detected (status={job.status}, worker={job.worker_id})")
                    job.status = "FAILED"
                    job.error_message = f"Job timeout after {JOB_TIMEOUT_MINUTES} minutes - worker likely crashed"
                    job.worker_id = None  # Libera para retry
                    job.updated_at = datetime.now(timezone.utc)
                
                if stale_jobs:
                    session.commit()
                    print(f"✅ Reset {len(stale_jobs)} timed-out jobs to FAILED state")
        
        except Exception as e:
            print(f"❌ Error in watchdog task: {e}")
        
        await asyncio.sleep(60)  # Check a cada minuto

@app.on_event("startup")
async def startup_event():
    """Inicia tarefas de background."""
    asyncio.create_task(job_timeout_watchdog())
    print("✅ Job timeout watchdog started")

@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc)}

# Dependência do Banco
def get_session():
    with Session(engine) as session:
        yield session

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
    jobs = session.exec(select(VideoJob)).all()
    return jobs

@app.post("/webhooks/hotmart", tags=["Finance"])
async def hotmart_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    data = await request.json()
    src = data.get("src") or data.get("hsrc")
    if src and "-" in src:
        try:
            _, video_id_str = src.split("-")
            video_uuid = UUID(video_id_str)
            commission_value = float(data.get("commission", {}).get("value", 0))
            background_tasks.add_task(update_roi, video_uuid, commission_value, session)
            return {"status": "assigned", "video_id": video_id_str}
        except: pass
    return {"status": "received"}

@app.post("/webhooks/clickbank", tags=["Finance"])
async def clickbank_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    data = await request.json()
    tid = data.get("trackingId")
    if tid and "-" in tid:
        try:
            _, video_id_str = tid.split("-")
            video_uuid = UUID(video_id_str)
            revenue = float(data.get("payoutAmount", 0))
            background_tasks.add_task(update_roi, video_uuid, revenue, session)
            return {"status": "assigned", "video_id": video_id_str}
        except: pass
    return {"status": "received"}

def update_roi(video_uuid: UUID, value: float, session: Session):
    job = session.get(VideoJob, video_uuid)
    if not job: return
    log = session.exec(select(PerformanceLog).where(PerformanceLog.video_id == video_uuid)).first()
    if not log:
        log = PerformanceLog(video_id=video_uuid, tenant_id=job.tenant_id)
        session.add(log)
    log.sales_count += 1
    log.affiliate_revenue += value
    log.last_updated = datetime.now(timezone.utc)
    session.commit()

from cloud_hub.core.intelligence import run_scout_phase, run_viral_score_phase
from cloud_hub.core.copywriter import run_copywriter_phase

@app.post("/admin/trigger-scout/{channel_id}", tags=["Intelligence"])
async def trigger_scout(channel_id: UUID, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if not channel: raise HTTPException(status_code=404, detail="Channel not found")
    job = await run_scout_phase(session, channel)
    score = await run_viral_score_phase(session, job)
    if score < 70: return {"status": "aborted", "reason": "Low viral score", "score": score}
    await run_copywriter_phase(session, job)
    return {"status": "success", "job_id": job.id, "topic": job.topic}

@app.get("/worker/next-job/{market_region}", tags=["Worker"])
def get_next_job(market_region: str, request: Request, session: Session = Depends(get_session)):
    """
    Retorna o próximo job disponível com lock otimista para evitar race conditions.
    Usa SELECT FOR UPDATE SKIP LOCKED para garantir que apenas 1 worker pegue o job.
    """
    # Tenta pegar um job APPROVED da região específica
    statement = (
        select(VideoJob)
        .where(VideoJob.status == "APPROVED")
        .where(VideoJob.worker_id.is_(None))
        .join(Channel)
        .where(Channel.market_region == market_region)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = session.exec(statement).first()
    
    # Fallback: pega job DRAFTED do super admin
    if not job:
        statement = (
            select(VideoJob)
            .where(VideoJob.status == "DRAFTED")
            .where(VideoJob.worker_id.is_(None))
            .join(Tenant)
            .where(Tenant.email == "derikmoretti@gmail.com")
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = session.exec(statement).first()
    
    if not job:
        return {"status": "idle"}
    
    # Marca job como PROCESSING atomicamente
    worker_id = request.headers.get("X-Worker-ID", f"worker-{datetime.now(timezone.utc).timestamp()}")
    job.status = "PROCESSING"
    job.worker_id = worker_id
    job.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(job)
    
    return {
        "status": "assigned",
        "job_id": str(job.id),
        "niche": job.topic or "unknown",
        "script_data": job.script_data
    }

@app.post("/worker/report-status/{job_id}", tags=["Worker"])
def report_status(job_id: UUID, status: str, error: Optional[str] = None, session: Session = Depends(get_session)):
    """
    Atualiza o status do job com validação de transições FSM (Finite State Machine).
    Previne transições inválidas que causam perda de estado.
    """
    job = session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    current_status = job.status
    
    # Validação de transição de estado
    valid_next_states = VALID_TRANSITIONS.get(current_status, [])
    if status not in valid_next_states:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid state transition: {current_status} -> {status}. Valid transitions: {valid_next_states}"
        )
    
    # Atualiza status e metadata
    job.status = status
    if error:
        job.error_message = error
    job.updated_at = datetime.now(timezone.utc)
    
    # Limpa worker_id se job falhou (permite retry por outro worker)
    if status == "FAILED":
        job.worker_id = None
    
    session.commit()
    return {"status": "ok", "transition": f"{current_status} -> {status}"}
