from __future__ import annotations
import os
from uuid import UUID, uuid4
from typing import List, Optional, Dict
from datetime import datetime, timezone
from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

# --- 1. Tenants (Empresas/Agencias) ---
class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    email: str = Field(unique=True, index=True)
    plan_type: str = "free"
    credits_balance: int = 0
    auto_approve_content: bool = False
    
    # Relacionamentos
    channels: List["Channel"] = Relationship(back_populates="tenant")

# --- 2. Canais (YouTube Channels) ---
class Channel(SQLModel, table=True):
    __tablename__ = "channels"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    youtube_channel_id: Optional[str] = None
    name: str
    niche: str
    language: str
    market_region: str
    active: bool = True
    
    # Vinculado ao Tenant
    tenant: Optional[Tenant] = Relationship(back_populates="channels")
    video_jobs: List["VideoJob"] = Relationship(back_populates="channel")

# --- 3. Video Jobs (Pipeline) ---
class VideoJob(SQLModel, table=True):
    __tablename__ = "video_jobs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    channel_id: UUID = Field(foreign_key="channels.id")
    
    status: str = Field(default="PENDING", index=True)
    topic: Optional[str] = None
    angle: Optional[str] = None
    
    # PostgreSQL JSONB fields (Mais robusto)
    target_keywords: List[str] = Field(default_factory=list, sa_column=Column(JSONB))
    viral_score: Optional[float] = None
    script_data: Dict = Field(default_factory=dict, sa_column=Column(JSONB))
    thumbnail_data: Dict = Field(default_factory=dict, sa_column=Column(JSONB))
    
    injected_affiliate_url: Optional[str] = None
    
    # Relacionamentos
    channel: Optional[Channel] = Relationship(back_populates="video_jobs")

# --- 4. Performance Logs (ROI) ---
class PerformanceLog(SQLModel, table=True):
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
    __tablename__ = "affiliate_products_cache"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id")
    platform: str
    product_name: str
    niche: str
    hotlink_base: str
    gravity: float = 0.0
    language: str
