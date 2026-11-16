import os
from uuid import UUID, uuid4
from typing import List, Optional, Dict
from datetime import datetime, timezone
from sqlmodel import Field, Relationship, SQLModel, JSON
from sqlalchemy import Column

# --- 1. Tenants (Empresas/Agencias) ---
class Tenant(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "tenants"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    email: str = Field(unique=True, index=True)
    plan_type: str = "free"
    credits_balance: int = 0
    auto_approve_content: bool = False
    
    # Relacionamento: Um Tenant tem vários Channels
    channels: List["Channel"] = Relationship(back_populates="tenant")
    region: str = Field(default="North America")

    # Novas colunas para Micro SaaS
    password_hash: Optional[str] = Field(default=None)
    personal_api_keys: Dict = Field(
        default_factory=lambda: {
            "gemini": "",
            "openai": "",
            "pexels": "",
            "pixabay": "",
            "deepseek": "",
            "anthropic": ""
        },
        sa_type=JSON
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# --- 2. Canais (YouTube Channels) ---
class Channel(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "channels"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    youtube_channel_id: Optional[str] = None
    name: str
    identifier: str = Field(index=True, unique=True, default="en-US")
    niche: str
    language: str
    market_region: str
    active: bool = True
    
    # Relacionamentos
    tenant: Optional[Tenant] = Relationship(back_populates="channels")
    video_jobs: List["VideoJob"] = Relationship(back_populates="channel")

# --- 3. Video Jobs (Pipeline) ---
class VideoJob(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "video_jobs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channels.id", index=True)
    
    status: str = Field(default="PENDING", index=True)
    topic: Optional[str] = None
    viral_score: Optional[float] = None
    script_data: Dict = Field(default_factory=dict, sa_type=JSON)
    thumbnail_data: Dict = Field(default_factory=dict, sa_type=JSON)
    injected_affiliate_url: Optional[str] = Field(default=None)
    
    # BUG FIX: video_type was used in global_scheduler but never defined in model
    video_type: str = Field(default="LONG", index=True)  # "LONG" or "SHORT"
    production_profile: Optional[str] = Field(default=None, index=True)  # "PILOT_5MIN", "STANDARD_10MIN", "DOCUMENTARY_20MIN", "SHORTS"
    video_director_mode: Optional[str] = Field(default=None, index=True)  # "legacy" | "director" | None (inherits env/default)
    # Store the final YouTube URL after publishing for analytics and deep links
    published_url: Optional[str] = Field(default=None)
    
    is_master: bool = True
    target_language: str = Field(default="en-US")
    performance_metrics: Dict = Field(
        default_factory=lambda: {
            "ctr": 0.0, 
            "hook_retention": 0.0, 
            "average_view_duration": 0.0,
            "views_24h": 0
        }, 
        sa_type=JSON
    )
    optimization_notes: Optional[str] = None
    is_cloned: bool = False
    parent_job_id: Optional[UUID] = Field(default=None, foreign_key="video_jobs.id")
    
    published_at: Optional[datetime] = None 
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: Optional[datetime] = Field(default=None, index=True)
    
    channel: Optional[Channel] = Relationship(back_populates="video_jobs")

# --- 4. Performance Logs (ROI) ---
class PerformanceLog(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "performance_logs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="video_jobs.id")
    tenant_id: UUID = Field(foreign_key="tenants.id")
    
    views_count: int = 0
    sales_count: int = 0
    affiliate_revenue: float = 0.0
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# --- 5. Cache de Produtos ---
class AffiliateProduct(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "affiliate_products_cache"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    platform: str
    product_name: str
    niche: str
    hotlink_base: str
    gravity: float = 0.0
    language: str

# --- 6. Viral Themes (Memória de Longo Prazo) ---
class ViralTheme(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    __tablename__ = "viral_themes"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    topic: str = Field(index=True)
    niche: str
    views_triggered: int = 0
    times_replicated: int = 0
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
