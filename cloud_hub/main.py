import os
import secrets
from uuid import UUID, uuid4
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request, Form, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, create_engine, select, SQLModel
from dotenv import load_dotenv
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from cloud_hub.models import Tenant, Channel, VideoJob, AffiliateProduct, PerformanceLog
from cloud_hub.core.intelligence import run_scout_phase, run_viral_score_phase
from cloud_hub.core.copywriter import run_copywriter_phase

from contextlib import asynccontextmanager

# =============================================================================
# 1. HASHING DE SENHAS (ARGON2ID) E BOOTSTRAP ADMINISTRATIVO CONTROLADO
# =============================================================================
_password_hasher = PasswordHasher()
_ALLOWED_BOOTSTRAP_PLANS = {"free", "starter", "pro", "enterprise"}


def hash_password(password: str) -> str:
    """Gera hash Argon2id seguro para armazenamento de senha."""
    if not password or not isinstance(password, str):
        raise ValueError("Senha inválida para geração de hash.")
    return _password_hasher.hash(password)


def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """Verifica senha contra hash Argon2id; rejeita hash ausente ou inválido."""
    if not stored_hash or not password:
        return False
    try:
        return _password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def _run_admin_bootstrap(session: Session) -> bool:
    """
    Executa bootstrap administrativo somente quando ENABLE_ADMIN_BOOTSTRAP=1/true
    e com configuração explícita e validada via variáveis de ambiente.
    """
    enabled = (os.getenv("ENABLE_ADMIN_BOOTSTRAP") or "").strip().lower()
    if enabled not in {"1", "true"}:
        return False

    admin_email = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    if not admin_email or "@" not in admin_email:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_EMAIL obrigatório e válido quando ENABLE_ADMIN_BOOTSTRAP está ativo."
        )

    admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not admin_password.strip():
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD obrigatória quando ENABLE_ADMIN_BOOTSTRAP está ativo."
        )

    admin_name = (os.getenv("BOOTSTRAP_ADMIN_NAME") or "Super Admin").strip() or "Super Admin"
    plan_type = (os.getenv("BOOTSTRAP_ADMIN_PLAN") or "free").strip().lower()
    if plan_type not in _ALLOWED_BOOTSTRAP_PLANS:
        raise ValueError(
            f"BOOTSTRAP_ADMIN_PLAN inválido: '{plan_type}'. Permitidos: {sorted(_ALLOWED_BOOTSTRAP_PLANS)}"
        )

    raw_credits = (os.getenv("BOOTSTRAP_ADMIN_CREDITS") or "0").strip()
    try:
        credits_balance = int(raw_credits)
    except ValueError as exc:
        raise ValueError(f"BOOTSTRAP_ADMIN_CREDITS inválido: '{raw_credits}'") from exc
    if credits_balance < 0:
        raise ValueError("BOOTSTRAP_ADMIN_CREDITS não pode ser negativo.")

    admin = session.exec(select(Tenant).where(Tenant.email == admin_email)).first()
    if not admin:
        admin = Tenant(
            name=admin_name,
            email=admin_email,
            plan_type=plan_type,
            credits_balance=credits_balance,
            auto_approve_content=False,
            password_hash=hash_password(admin_password),
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
    elif not admin.password_hash:
        raise RuntimeError(
            "Administrador preexistente sem password_hash; não será sobrescrito silenciosamente."
        )

    create_master = (os.getenv("BOOTSTRAP_CREATE_MASTER_CHANNEL") or "").strip().lower()
    if create_master in {"1", "true"}:
        master_channel = session.exec(
            select(Channel).where(
                Channel.tenant_id == admin.id,
                Channel.language == "en-US",
            )
        ).first()
        if not master_channel:
            new_channel = Channel(
                tenant_id=admin.id,
                name="Master AI Stories US",
                niche="AI + Money + Stories",
                language="en-US",
                market_region="North America",
                active=True,
            )
            session.add(new_channel)
            session.commit()

    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine:
        with Session(engine) as session:
            _run_admin_bootstrap(session)
    yield

# =============================================================================
# 2. INICIALIZAÇÃO DA APLICAÇÃO (Ligando o lifespan)
# =============================================================================
app = FastAPI(
    title="Dark Channel Factory Cloud-Hub API", 
    version="2.1",
    lifespan=lifespan # <--- IMPORTANTE: Ativa a automação de arranque
)

# =============================================================================
# 1. CONFIGURAÇÕES INICIAIS E BANCO DE DADOS
# =============================================================================
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    separator = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL += f"{separator}sslmode=require"

def create_resilient_engine():
    if DATABASE_URL:
        try:
            eng = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
            with eng.connect() as conn:
                pass
            return eng
        except Exception as exc:
            print(f"⚠️ [CloudHub] Banco de dados remoto indisponível ({exc}). Ativando fallback para SQLite local...")
    
    sqlite_path = os.path.abspath("output/logs/factory.db")
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
    eng = create_engine(f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng

engine = create_resilient_engine()

def get_session():
    if not engine:
        raise HTTPException(status_code=500, detail="Database not configured")
    with Session(engine) as session:
        yield session

# =============================================================================
# 2. INICIALIZAÇÃO DA APLICAÇÃO (O "app" nasce aqui)
# =============================================================================
# A segunda instância abaixo foi REMOVIDA - estava sobrescrevendo o app com lifespan

# =============================================================================
# 3. SEGURANÇA E AUTH (SESSÃO VIA COOKIE)
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates_path = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_path)

def get_current_tenant(request: Request, session: Session = Depends(get_session)) -> Tenant:
    tenant_id = request.cookies.get("session_tenant_id") or request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    
    try:
        tenant_uuid = UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Identificador de sessão inválido")

    tenant = session.get(Tenant, tenant_uuid)
    if not tenant:
        raise HTTPException(status_code=401, detail="Sessão inválida")
    return tenant

# Super Admin fallback (Basic Auth)
security = HTTPBasic()
def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = os.getenv("ADMIN_USER")
    correct_pass = os.getenv("ADMIN_PASS")
    if not correct_user or not correct_pass:
        raise HTTPException(
            status_code=503,
            detail="Autenticação administrativa não configurada no ambiente",
        )
    if not (secrets.compare_digest(credentials.username, correct_user) and
            secrets.compare_digest(credentials.password, correct_pass)):
        raise HTTPException(status_code=401, detail="Credenciais Inválidas", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# =============================================================================
# 4. ROTAS PRINCIPAIS
# =============================================================================

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def read_dashboard(request: Request, session: Session = Depends(get_session)):
    tenant_id = request.cookies.get("session_tenant_id")
    if not tenant_id:
        return RedirectResponse(url="/login")
    
    tenant = session.get(Tenant, UUID(tenant_id))
    # Filtra jobs apenas deste Tenant
    jobs = session.exec(
        select(VideoJob)
        .join(Channel)
        .where(Channel.tenant_id == tenant.id)
        .order_by(VideoJob.created_at.desc())
    ).all()
    
    channels = session.exec(select(Channel).where(Channel.tenant_id == tenant.id)).all()
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "jobs": jobs,
            "channels": channels,
            "stats": {
                "total": len(jobs),
                "published": len([j for j in jobs if j.status == "PUBLISHED"]),
                "user": tenant.name
            }
        }
    )

# --- LOGIN / LOGOUT ---
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_action(response: Response, username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    # Busca Tenant por email
    tenant = session.exec(select(Tenant).where(Tenant.email == username)).first()
    
    # Fail-closed: exige hash Argon2id válido (sem senha padrão ou texto puro)
    if tenant and verify_password(tenant.password_hash, password):
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(key="session_tenant_id", value=str(tenant.id))
        return resp
            
    return RedirectResponse(url="/login?error=1", status_code=303)

@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("session_tenant_id")
    return resp

# --- CONFIGURAÇÕES (API KEYS) ---
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tenant: Tenant = Depends(get_current_tenant)):
    return templates.TemplateResponse("settings.html", {"request": request, "tenant": tenant})

@app.post("/settings")
def save_settings(
    gemini: str = Form(""), openai: str = Form(""), pexels: str = Form(""), 
    pixabay: str = Form(""), deepseek: str = Form(""), anthropic: str = Form(""),
    tenant: Tenant = Depends(get_current_tenant), session: Session = Depends(get_session)
):
    tenant.personal_api_keys = {
        "gemini": gemini, "openai": openai, "pexels": pexels,
        "pixabay": pixabay, "deepseek": deepseek, "anthropic": anthropic
    }
    session.add(tenant)
    session.commit()
    return RedirectResponse(url="/settings?success=1", status_code=303)

# --- CANAIS E NICHOS ---
@app.get("/channels", response_class=HTMLResponse)
def channels_page(request: Request, tenant: Tenant = Depends(get_current_tenant), session: Session = Depends(get_session)):
    channels = session.exec(select(Channel).where(Channel.tenant_id == tenant.id)).all()
    return templates.TemplateResponse("channels.html", {"request": request, "tenant": tenant, "channels": channels})

@app.post("/channels")
def add_channel(
    name: str = Form(...), niche: str = Form(...), youtube_channel_id: str = Form(""), 
    language: str = Form("pt-BR"), tenant: Tenant = Depends(get_current_tenant), 
    session: Session = Depends(get_session)
):
    new_channel = Channel(
        tenant_id=tenant.id,
        name=name,
        niche=niche,
        youtube_channel_id=youtube_channel_id,
        language=language,
        market_region="Default",
        active=True
    )
    session.add(new_channel)
    session.commit()
    return RedirectResponse(url="/channels", status_code=303)

@app.get("/api/health", tags=["System"])
def health_check():
    return {"status": "healthy", "server": "vercel", "db": "connected" if engine else "error"}


# =============================================================================
# 5. GESTÃO DE TENANTS & ADMIN
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
    jobs = session.exec(select(VideoJob)).all()
    return jobs


# =============================================================================
# 6. SMART MONETIZATION & ANALYST WEBHOOKS
# =============================================================================

@app.post("/webhooks/hotmart", tags=["Finance"])
async def hotmart_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    data = await request.json()
    src = data.get("src") or data.get("hsrc")
    
    if src and "-" in src:
        try:
            # Tracking code format: channel_id[:8]-video_uuid, so split on first '-' only
            parts = src.split("-", 1)
            if len(parts) == 2:
                video_id_str = parts[1]
                video_uuid = UUID(video_id_str)
                commission_value = float(data.get("commission", {}).get("value", 0))
                background_tasks.add_task(update_roi, video_uuid, commission_value, session)
                return {"status": "assigned", "video_id": video_id_str}
        except:
            pass

    return {"status": "received", "msg": "No tracking ID found"}


@app.post("/webhooks/clickbank", tags=["Finance"])
async def clickbank_webhook(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    data = await request.json()
    tid = data.get("trackingId")
    
    if tid and "-" in tid:
        try:
            # Tracking code format: channel_id[:8]-video_uuid, so split on first '-' only
            parts = tid.split("-", 1)
            if len(parts) == 2:
                video_id_str = parts[1]
                video_uuid = UUID(video_id_str)
                revenue = float(data.get("payoutAmount", 0))
                background_tasks.add_task(update_roi, video_uuid, revenue, session)
                return {"status": "assigned", "video_id": video_id_str}
        except:
            pass
            
    return {"status": "received"}


def update_roi(video_uuid: UUID, value: float, session: Session):
    job = session.get(VideoJob, video_uuid)
    if not job:
        return

    log = session.exec(select(PerformanceLog).where(PerformanceLog.video_id == video_uuid)).first()
    if not log:
        # BUG FIX: VideoJob doesn't have tenant_id directly; must get it through channel
        channel = session.get(Channel, job.channel_id)
        tenant_id = channel.tenant_id if channel else None
        log = PerformanceLog(video_id=video_uuid, tenant_id=tenant_id)
        session.add(log)
    
    log.sales_count += 1
    log.affiliate_revenue += value
    log.last_updated = datetime.now(timezone.utc)
    
    session.commit()


# =============================================================================
# 7. PIPELINE DE INTELIGÊNCIA E OTIMIZAÇÃO (CLOUD)
# =============================================================================

@app.post("/admin/trigger-scout/{channel_id}", tags=["Intelligence"])
async def trigger_scout(
    channel_id: UUID, 
    production_profile: Optional[str] = None, 
    session: Session = Depends(get_session)
):
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
        
    job = await run_scout_phase(session, channel, production_profile=production_profile)
    score = await run_viral_score_phase(session, job)
    
    if score < 70:
        return {"status": "aborted", "reason": "Low viral score", "score": score}
        
    await run_copywriter_phase(session, job)
    
    return {
        "status": "success", 
        "job_id": job.id, 
        "topic": job.topic, 
        "affiliate": job.injected_affiliate_url,
        "production_profile": job.production_profile
    }

from pydantic import BaseModel, Field

class JobCreateRequest(BaseModel):
    channel_id: UUID
    topic: str
    production_profile: Optional[str] = None
    video_type: str = "LONG"
    video_director_mode: Optional[str] = None

@app.post("/jobs/create", tags=["Jobs"])
def create_video_job(
    payload: JobCreateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    session: Session = Depends(get_session)
):
    # 1. Validação de campos obrigatórios e sanitização
    clean_topic = payload.topic.strip()
    if not clean_topic:
        raise HTTPException(status_code=400, detail="O campo 'topic' não pode ser vazio")

    clean_video_type = payload.video_type.strip().upper()
    if clean_video_type not in ["LONG", "SHORT"]:
        raise HTTPException(status_code=400, detail="O campo 'video_type' deve ser 'LONG' ou 'SHORT'")

    # 2. Validação estrita de production_profile
    validated_profile = None
    if payload.production_profile is not None:
        raw_prof = str(payload.production_profile).strip().upper()
        if raw_prof:
            from shared.production_profiles import PROFILES, _PROFILE_ALIASES
            resolved_key = _PROFILE_ALIASES.get(raw_prof, raw_prof)
            if resolved_key not in PROFILES:
                valid_profiles = list(PROFILES.keys())
                raise HTTPException(
                    status_code=400,
                    detail=f"Perfil de produção inválido: '{payload.production_profile}'. Valores permitidos: {valid_profiles}"
                )
            validated_profile = resolved_key

    # 2.5 Validação estrita de video_director_mode
    validated_director_mode = None
    if payload.video_director_mode is not None:
        from cloud_hub.core.video_director_integration import (
            InvalidVideoDirectorModeError,
            _normalize_mode_value,
        )
        try:
            validated_director_mode = _normalize_mode_value(payload.video_director_mode, "request")
        except InvalidVideoDirectorModeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # 3. Autorização e Isolamento Multitenant (Proteção contra criação em nome de terceiros)
    channel = session.get(Channel, payload.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal não encontrado")

    is_super_admin = (tenant.email == "derikmoretti@gmail.com")
    if channel.tenant_id != tenant.id and not is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Não autorizado: o canal especificado não pertence à sua organização"
        )

    # 4. Criação e persistência do job
    job = VideoJob(
        channel_id=channel.id,
        topic=clean_topic,
        video_type=clean_video_type,
        production_profile=validated_profile,
        video_director_mode=validated_director_mode,
        status="APPROVED",
        target_language=channel.language,
        is_master=True
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return {
        "status": "success",
        "job_id": job.id,
        "topic": job.topic,
        "production_profile": job.production_profile,
        "video_type": job.video_type,
        "channel_id": job.channel_id,
        "tenant_id": tenant.id
    }

@app.post("/admin/diagnose-and-model/{job_id}", tags=["Optimization"])
def diagnose_video_performance(job_id: UUID, session: Session = Depends(get_session)):
    job = session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    metrics = job.performance_metrics or {}
    
    HOOK_THRESHOLD = 60.0
    CTR_THRESHOLD = 6.0
    needs_rework = []
    
    if metrics.get("hook_retention", 0.0) < HOOK_THRESHOLD:
        competitor_analysis = run_scout_phase(topic=job.topic, focus="hook_structure")
        job.optimization_notes = f"Hook fraco. Modelar: {competitor_analysis['top_hook_pattern']}"
        needs_rework.append("HOOK")
        
    if metrics.get("ctr", 0.0) < CTR_THRESHOLD:
        thumb_analysis = run_scout_phase(topic=job.topic, focus="thumbnail_visuals")
        if job.optimization_notes:
            job.optimization_notes += f" | CTR baixo. Usar: {thumb_analysis['winning_color_scheme']}"
        else:
            job.optimization_notes = f"CTR baixo. Usar: {thumb_analysis['winning_color_scheme']}"
        needs_rework.append("THUMBNAIL")
        
    session.add(job)
    session.commit()
    
    return {"needs_rework": needs_rework, "advice": job.optimization_notes}

@app.post("/admin/auto-scale-top20", tags=["Scaling Engine"])
def auto_scale_top_20_percent(tenant_id: UUID, session: Session = Depends(get_session)):
    master_jobs = session.exec(
        select(VideoJob).where(
            VideoJob.is_master == True,
            VideoJob.status == "PUBLISHED",
            VideoJob.is_cloned == False
        )
    ).all()
    
    if not master_jobs:
        return {"message": "Nenhum vídeo novo para analisar."}

    def calculate_power(job: VideoJob):
        metrics = job.performance_metrics or {}
        ctr = metrics.get("ctr", 0.0)
        retention = metrics.get("retention", 0.0)
        return ctr * retention

    ranked_jobs = sorted(master_jobs, key=calculate_power, reverse=True)
    top_20_count = max(1, int(len(ranked_jobs) * 0.20))
    elite_videos = ranked_jobs[:top_20_count]
    
    clones_channels = session.exec(
        select(Channel).where(
            Channel.tenant_id == tenant_id,
            Channel.niche == "AI + Money + Stories",
            Channel.language.in_(["es-ES", "de-DE", "fr-FR", "ar-SA"])
        )
    ).all()

    clones_created = 0
    for master in elite_videos:
        for clone_channel in clones_channels:
            new_job = VideoJob(
                channel_id=clone_channel.id,
                status="PENDING_TRANSLATION",
                topic=master.topic,
                is_master=False,
                parent_job_id=master.id,
                target_language=clone_channel.language,
                script_data=master.script_data,
                production_profile=master.production_profile
            )
            session.add(new_job)
            clones_created += 1
            
        master.is_cloned = True
        session.add(master)

    session.commit()
    
    return {
        "status": "success",
        "analyzed_videos": len(ranked_jobs),
        "elite_videos_selected": len(elite_videos),
        "clones_dispatched": clones_created,
        "message": "Filtro Top 20% aplicado com sucesso. Fábrica de clones ativada!"
    }

@app.post("/admin/validate-and-clone/{job_id}", tags=["Scaling Engine"])
def validate_and_clone_video(job_id: UUID, session: Session = Depends(get_session)):
    master_job = session.get(VideoJob, job_id)
    if not master_job or not master_job.is_master:
        raise HTTPException(status_code=400, detail="Job não encontrado ou não é um Master (Inglês)")
        
    metrics = master_job.performance_metrics
    ctr = metrics.get("ctr", 0.0)
    retention = metrics.get("retention", 0.0)
    
    if ctr < 6.0 or retention < 40.0:
        return {"status": "rejected", "message": f"Vídeo flopou (CTR: {ctr}%, Ret: {retention}%). Não será replicado."}
        
    clones_channels = session.exec(
        select(Channel).where(
            Channel.tenant_id == master_job.channel_id,
            Channel.niche == "AI + Money + Stories",
            Channel.language.in_(["es-ES", "de-DE", "fr-FR", "ar-SA"])
        )
    ).all()
    
    cloned_jobs = []
    for clone_channel in clones_channels:
        new_job = VideoJob(
            channel_id=clone_channel.id,
            status="PENDING_TRANSLATION",
            topic=master_job.topic,
            is_master=False,
            parent_job_id=master_job.id,
            target_language=clone_channel.language,
            script_data=master_job.script_data,
            production_profile=master_job.production_profile
        )
        session.add(new_job)
        cloned_jobs.append(new_job)
        
    session.commit()
    
    return {
        "status": "success", 
        "message": "Vídeo validado! Clones gerados para a rede.",
        "clones_created": len(cloned_jobs)
    }


# =============================================================================
# 8. INTERFACE COM O WORKER LOCAL E CRONS
# =============================================================================

@app.get("/worker/next-job/{market_region}", tags=["Worker"])
def get_next_job(market_region: str, session: Session = Depends(get_session)):
    # ✅ A SOLUÇÃO (Join explícito)
    statement = (
    select(VideoJob)
    .join(Channel, VideoJob.channel_id == Channel.id)
    .join(Tenant, Channel.tenant_id == Tenant.id)
    .where(
        Tenant.region == market_region,
        VideoJob.status.in_(["DRAFTED", "APPROVED", "READY_FOR_EDIT"])
       )
    )
    
    job = session.exec(statement).first()
    
    if not job:
        statement = select(VideoJob).where(
            VideoJob.status.in_(["DRAFTED", "APPROVED", "READY_FOR_EDIT"])
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
        "injected_url": job.injected_affiliate_url,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "video_type": job.video_type,
        "production_profile": getattr(job, "production_profile", None),
    }

@app.post("/worker/report-status/{job_id}", tags=["Worker"])
def report_status(job_id: UUID, status: str, error: Optional[str] = None, session: Session = Depends(get_session)):
    job = session.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.status = status
    if error:
        job.error_message = error
    job.updated_at = datetime.now(timezone.utc)
    
    session.commit()
    return {"status": "ok"}

@app.get("/api/cron/trigger-master", tags=["Cron"])
def cron_trigger_master(session: Session = Depends(get_session)):
    master_channel = session.exec(
        select(Channel).where(
            Channel.niche == "AI + Money + Stories",
            Channel.language == "en-US",
            Channel.active == True
        )
    ).first()

    if not master_channel:
        return {"status": "error", "message": "Canal Master (Inglês) não encontrado."}

    new_job = VideoJob(
        tenant_id=master_channel.tenant_id,
        channel_id=master_channel.id,
        status="DRAFTED",
        is_master=True,
        target_language="en-US",
        topic="AI Money automation ideas"
    )
    
    session.add(new_job)
    session.commit()

    return {"status": "success", "message": "Fábrica acionada! Novo vídeo em inglês adicionado à fila."}