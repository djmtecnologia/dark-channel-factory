"""
Shared Pydantic Models — Dark Channel Factory
Objetos de dados trocados entre os agentes via filas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────
# Enumerações
# ─────────────────────────────────────────────────────────────

class Niche(str, Enum):
    AI_TOOLS = "ai_tools"
    PERSONAL_FINANCE = "personal_finance"
    BUSINESS_AUTOMATION = "business_automation"
    HEALTH_WELLNESS = "health_wellness"
    REAL_ESTATE = "real_estate"
    CRYPTO = "crypto"
    ECOMMERCE = "ecommerce"


class SceneType(str, Enum):
    HOOK = "hook"
    PROBLEM = "problem"
    AGITATION = "agitation"
    SOLUTION = "solution"
    PROOF = "proof"
    OBJECTION = "objection"
    CTA = "cta"
    OUTRO = "outro"


class Emotion(str, Enum):
    SHOCK = "shock"
    CURIOSITY = "curiosity"
    URGENCY = "urgency"
    TRUST = "trust"
    EXCITEMENT = "excitement"
    FEAR = "fear"
    HOPE = "hope"


class AffiliatePlatform(str, Enum):
    AMAZON = "amazon"
    CLICKBANK = "clickbank"
    HOTMART = "hotmart"
    GUMROAD = "gumroad"
    DIRECT = "direct"


class VideoStatus(str, Enum):
    PENDING = "pending"
    SCRIPTING = "scripting"
    PRODUCING = "producing"
    DESIGNING = "designing"
    UPLOADING = "uploading"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    MONITORING = "monitoring"
    DEEP_DIVE_TRIGGERED = "deep_dive_triggered"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────────
# Agent 1 → Agent 2: NicheData
# ─────────────────────────────────────────────────────────────

class NicheData(BaseModel):
    """Output do Scout & Miner Agent — input do Copywriter Agent."""
    job_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Conteúdo
    topic: str = Field(..., description="Tópico principal do vídeo")
    language: str = Field(default="en-US", description="BCP-47 locale")
    voice: str = Field(default="en-US-AriaNeural", description="Voz edge-tts")
    niche: Niche = Field(default=Niche.AI_TOOLS)

    # SEO
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    lsi_keywords: list[str] = Field(default_factory=list)

    # Estratégia
    angle: str = Field(..., description="Ângulo de abordagem / gancho estratégico")
    content_gap: str = Field(..., description="O que os concorrentes não cobrem")
    target_audience: str = Field(default="professionals aged 25-45")

    # Métricas estimadas
    estimated_cpm_usd: float = Field(default=0.0)
    niche_score: int = Field(default=0, ge=0, le=100, description="Score de oportunidade 0-100")
    competition_level: str = Field(default="medium")  # low / medium / high

    # Deep Dive
    is_deep_dive: bool = Field(default=False)
    parent_video_id: str | None = Field(default=None)
    parent_topic: str | None = Field(default=None)

    # Canal
    channel_id: str = Field(default="default")


# ─────────────────────────────────────────────────────────────
# Agent 2 → Agents 3+4: ScriptData
# ─────────────────────────────────────────────────────────────

class AffiliateLink(BaseModel):
    platform: AffiliatePlatform
    product_name: str
    product_description: str
    url: str
    commission_percent: float = Field(default=0.0)


class Scene(BaseModel):
    id: int
    scene_type: SceneType
    text: str = Field(..., description="Texto narrado desta cena")
    duration_s: float = Field(..., description="Duração estimada em segundos")
    emotion: Emotion = Field(default=Emotion.CURIOSITY)
    b_roll_query: str = Field(..., description="Query para buscar vídeo de fundo")
    on_screen_text: str | None = Field(default=None, description="Texto na tela (máx 3 palavras)")
    sfx_cue: str = Field(default="none", description="Efeito sonoro sugerido (whoosh, click, impact, etc)")


class ScriptData(BaseModel):
    """Output do Copywriter Agent — input dos Agents 3 e 4."""
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    niche_data: NicheData

    # Metadados YouTube
    title: str = Field(..., max_length=100)
    description: str = Field(..., max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=500)
    category_id: str = Field(default="28")  # 28 = Science & Technology
    language: str = Field(default="en-US")

    # Afiliados
    affiliate_links: list[AffiliateLink] = Field(default_factory=list)
    pinned_comment: str = Field(default="", description="Primeiro comentário fixado com links")

    # Roteiro
    scenes: list[Scene] = Field(default_factory=list)
    total_duration_s: float = Field(default=0.0)
    music_mood: str = Field(default="suspense", description="Mood da trilha sonora (suspense, epic, tech, etc)")

    # Thumbnail brief
    thumbnail_brief: str = Field(..., description="Briefing para o Designer Agent")
    thumbnail_dominant_color: str = Field(default="#FF6B35")


# ─────────────────────────────────────────────────────────────
# Agent 3: VideoData
# ─────────────────────────────────────────────────────────────

class VideoData(BaseModel):
    """Output do Video Editor Agent — input do Manager Agent."""
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    script_data: ScriptData

    # Arquivos gerados
    video_path: str = Field(..., description="Caminho absoluto do .mp4 final")
    audio_path: str = Field(..., description="Caminho do .mp3 narração completa")
    subtitle_path: str | None = Field(default=None, description="Caminho do .srt")

    # Métricas de produção
    actual_duration_s: float = Field(default=0.0)
    file_size_mb: float = Field(default=0.0)
    resolution: str = Field(default="1920x1080")


# ─────────────────────────────────────────────────────────────
# Agent 4: ThumbnailData
# ─────────────────────────────────────────────────────────────

class ThumbnailVariant(BaseModel):
    variant: str  # "A", "B", "C"
    path: str
    prompt_used: str
    is_active: bool = Field(default=False)


class ThumbnailData(BaseModel):
    """Output do Designer Agent — input do Manager Agent."""
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    variants: list[ThumbnailVariant] = Field(default_factory=list)
    active_variant: str = Field(default="A")

    def get_active_path(self) -> str | None:
        for v in self.variants:
            if v.variant == self.active_variant:
                return v.path
        return self.variants[0].path if self.variants else None


# ─────────────────────────────────────────────────────────────
# Agent 5: PublishedVideo + Analytics
# ─────────────────────────────────────────────────────────────

class PublishedVideo(BaseModel):
    """Registro de vídeo publicado no YouTube."""
    job_id: UUID
    youtube_video_id: str
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_publish_at: datetime | None = Field(default=None)
    title: str
    status: VideoStatus = Field(default=VideoStatus.PUBLISHED)

    # Paths locais
    video_path: str
    thumbnail_paths: list[str] = Field(default_factory=list)
    active_thumbnail_variant: str = Field(default="A")

    # Analytics snapshot (atualizado a cada 48h)
    views: int = Field(default=0)
    likes: int = Field(default=0)
    comments: int = Field(default=0)
    ctr_percent: float = Field(default=0.0)
    avg_view_duration_percent: float = Field(default=0.0)
    last_analytics_check: datetime | None = Field(default=None)

    # Feedback loop flags
    thumbnail_swapped_count: int = Field(default=0)
    deep_dive_triggered: bool = Field(default=False)

    # Afiliados
    affiliate_links: list[AffiliateLink] = Field(default_factory=list)
    pinned_comment_id: str | None = Field(default=None)

    # Referência ao script original
    niche_data: NicheData | None = Field(default=None)


class AnalyticsReport(BaseModel):
    """Relatório de analytics de um vídeo."""
    video_id: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    views: int = 0
    likes: int = 0
    comments: int = 0
    ctr_percent: float = 0.0
    avg_view_duration_s: float = 0.0
    avg_view_duration_percent: float = 0.0
    impressions: int = 0
    subscribers_gained: int = 0
    estimated_revenue_usd: float = 0.0

    @property
    def performance_grade(self) -> str:
        if self.ctr_percent >= 8:
            return "EXCELLENT"
        elif self.ctr_percent >= 5:
            return "GOOD"
        elif self.ctr_percent >= 4:
            return "AVERAGE"
        else:
            return "POOR"


# ─────────────────────────────────────────────────────────────
# Pipeline Job (estado global de cada vídeo)
# ─────────────────────────────────────────────────────────────

class PipelineJob(BaseModel):
    """Estado completo de um job de produção de vídeo."""
    job_id: UUID = Field(default_factory=uuid4)
    channel_id: str = Field(default="default")
    status: VideoStatus = Field(default=VideoStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = Field(default=None)
    retry_count: int = Field(default=0)

    # Dados progressivos (preenchidos conforme pipeline avança)
    niche_data: NicheData | None = Field(default=None)
    script_data: ScriptData | None = Field(default=None)
    video_data: VideoData | None = Field(default=None)
    thumbnail_data: ThumbnailData | None = Field(default=None)
    published_video: PublishedVideo | None = Field(default=None)

    model_config = ConfigDict(use_enum_values=True)

    def update_status(self, new_status: VideoStatus) -> None:
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
