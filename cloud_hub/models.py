from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel, JSON


# --- 1. Tenants ---
class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    plan_type: str = Field(default="free")  # 'free', 'pro', 'enterprise'
    credits_balance: int = Field(default=0)
    
    # Affiliate Settings
    hotmart_affiliate_id: Optional[str] = None
    clickbank_nickname: Optional[str] = None
    amazon_tag: Optional[str] = None
    
    auto_approve_content: bool = Field(default=False)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    channels: List["Channel"] = Relationship(back_populates="tenant")
    video_jobs: List["VideoJob"] = Relationship(back_populates="tenant")


# --- 2. Channels ---
class Channel(SQLModel, table=True):
    __tablename__ = "channels"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    
    youtube_channel_id: Optional[str] = None
    name: str
    niche: str
    language: str  # BCP-47 (e.g., 'en-US')
    market_region: str  # e.g., 'US', 'ES', 'BR'
    active: bool = Field(default=True)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    tenant: Tenant = Relationship(back_populates="channels")
    video_jobs: List["VideoJob"] = Relationship(back_populates="channel")


# --- 3. Affiliate Products Cache ---
class AffiliateProduct(SQLModel, table=True):
    __tablename__ = "affiliate_products_cache"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    
    platform: str  # 'HOTMART', 'CLICKBANK', 'AMAZON'
    product_name: str
    niche: str
    hotlink_base: str
    gravity: float = Field(default=0.0)
    commission_pct: float = Field(default=0.0)
    language: str
    
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- 4. Video Jobs ---
class VideoJob(SQLModel, table=True):
    __tablename__ = "video_jobs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    channel_id: Optional[UUID] = Field(foreign_key="channels.id", index=True)
    
    status: str = Field(default="PENDING")  # 'PENDING', 'SCOUTED', 'DRAFTED', 'APPROVED', 'RENDERED', 'PUBLISHED', 'FAILED'
    
    topic: Optional[str] = None
    angle: Optional[str] = None
    target_keywords: List[str] = Field(default_factory=list, sa_column=Field(JSON))
    viral_score: Optional[float] = None
    
    # AI Content Data (Scripts, Visual Prompts, etc.)
    script_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Field(JSON))
    thumbnail_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Field(JSON))
    
    injected_affiliate_url: Optional[str] = None
    
    # Worker Tracking
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    tenant: Tenant = Relationship(back_populates="video_jobs")
    channel: Optional[Channel] = Relationship(back_populates="video_jobs")


# --- 5. Performance Logs ---
class PerformanceLog(SQLModel, table=True):
    __tablename__ = "performance_logs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="video_jobs.id", index=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", index=True)
    
    youtube_video_id: Optional[str] = None
    
    # Metrics
    views_count: int = Field(default=0)
    ctr_percent: float = Field(default=0.0)
    retention_percent: float = Field(default=0.0)
    
    # Financial ROI
    clicks_count: int = Field(default=0)
    sales_count: int = Field(default=0)
    affiliate_revenue: float = Field(default=0.0)
    
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
