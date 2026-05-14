"""
Models V2 — Dark Channel Factory
Novos modelos Pydantic para os agentes da versão 2.
Importados automaticamente via shared/models.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# Viral Score Agent (Módulo 2 — Gatekeeper pré-produção)
# ─────────────────────────────────────────────────────────────

class ViralScoreBreakdown(BaseModel):
    """Breakdown dos 5 critérios do score viral (20pts cada)."""
    title_power: float = Field(default=0.0, ge=0, le=20,
                                description="Power words, emoção, urgência no título")
    curiosity_gap: float = Field(default=0.0, ge=0, le=20,
                                  description="Gap de curiosidade / open loop implícito")
    search_volume: float = Field(default=0.0, ge=0, le=20,
                                  description="Volume de busca estimado pelo keyword")
    competition_gap: float = Field(default=0.0, ge=0, le=20,
                                    description="Oportunidade vs. concorrência atual")
    trend_factor: float = Field(default=0.0, ge=0, le=20,
                                 description="Alinhamento com tendências do momento")

    @property
    def total(self) -> float:
        return round(
            self.title_power + self.curiosity_gap +
            self.search_volume + self.competition_gap + self.trend_factor, 1
        )


class ViralScoreResult(BaseModel):
    """Resultado do Viral Score Agent — gatekeeper da pipeline."""
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Dados de entrada
    topic: str
    angle: str
    language: str
    niche: str

    # Score
    score: float = Field(..., ge=0, le=100)
    breakdown: ViralScoreBreakdown
    passed_gate: bool = Field(default=False, description="True se score >= threshold (padrão 70)")
    rejection_reason: str | None = Field(default=None)

    # Otimizações automáticas (se score < 70, LLM sugere melhorias)
    improvements: list[str] = Field(default_factory=list)
    optimized_topic: str | None = Field(default=None)
    optimized_angle: str | None = Field(default=None)
    retry_count: int = Field(default=0, description="Número de tentativas de otimização")


# ─────────────────────────────────────────────────────────────
# Content Validator (Módulo 3 — QA do Roteiro)
# ─────────────────────────────────────────────────────────────

class ContentValidationResult(BaseModel):
    """Resultado do Content Validator Agent."""
    job_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    passed: bool = Field(default=False)

    # Scores individuais (0-100 cada)
    originality_score: float = Field(default=0.0, ge=0, le=100,
                                      description="Originalidade vs banco histórico")
    narrative_quality_score: float = Field(default=0.0, ge=0, le=100,
                                            description="Qualidade narrativa (hook, flow, CTA)")
    transformative_value_score: float = Field(default=0.0, ge=0, le=100,
                                               description="Valor transformador para o viewer")

    issues: list[str] = Field(default_factory=list,
                               description="Problemas críticos encontrados")
    suggestions: list[str] = Field(default_factory=list,
                                    description="Sugestões de melhoria não-críticas")

    # Estatísticas do roteiro
    word_count: int = Field(default=0)
    estimated_duration_s: float = Field(default=0.0)
    has_strong_hook: bool = Field(default=False)
    has_open_loops: bool = Field(default=False)
    has_clear_cta: bool = Field(default=False)
    affiliate_links_count: int = Field(default=0)


# ─────────────────────────────────────────────────────────────
# Omnichannel Shorts Agent (Módulo 8)
# ─────────────────────────────────────────────────────────────

class ShortPlatform(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"


class ShortClip(BaseModel):
    """Um clipe vertical 9:16 extraído do vídeo longo."""
    clip_index: int                         # 1, 2, 3
    source_video_path: str
    output_path: str                        # 9:16 mp4 (1080x1920)
    start_s: float                          # Início no vídeo original
    end_s: float                            # Fim no vídeo original
    duration_s: float                       # Duração real (max 60s para Shorts)
    hook_text: str                          # Texto overlay primeiros 3s
    cta_text: str = "Full video in bio ↑"  # CTA final
    related_video_id: str | None = None     # YouTube video ID do pai (para vincular)
    platform_video_ids: dict[str, str] = Field(default_factory=dict)  # platform → video_id
    published_at: dict[str, datetime] = Field(default_factory=dict)   # platform → timestamp


class ShortData(BaseModel):
    """Output do Shorts Agent."""
    job_id: UUID
    parent_youtube_video_id: str
    parent_title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    clips: list[ShortClip] = Field(default_factory=list)
    platforms_published: list[ShortPlatform] = Field(default_factory=list)
    total_shorts_views: int = Field(default=0)


# ─────────────────────────────────────────────────────────────
# Analyst & Learning Agent (Módulo 9)
# ─────────────────────────────────────────────────────────────

class WinningPattern(BaseModel):
    """Padrão de sucesso aprendido pelo sistema."""
    pattern_type: str               # 'title_structure' | 'thumbnail_style' | 'hook_type' | 'niche_keyword'
    pattern_value: str
    avg_ctr: float = 0.0
    avg_retention: float = 0.0
    avg_views: int = 0
    sample_size: int = 0
    niche: str
    language: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LearningInsight(BaseModel):
    """Insight gerado pelo Analyst Agent → realimenta o Scout Agent."""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    period_days: int = Field(default=30)

    # Top performers
    top_niches: list[str] = Field(default_factory=list)
    top_title_patterns: list[str] = Field(default_factory=list)
    top_thumbnail_styles: list[str] = Field(default_factory=list)
    top_hooks: list[str] = Field(default_factory=list)

    # Recomendações de replicação cross-language
    replicate_topics: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tópicos vencedores para replicar em outros idiomas"
    )

    # Affiliate performance
    top_affiliate_products: list[str] = Field(default_factory=list)
    avg_affiliate_ctr: float = Field(default=0.0)

    # Canais recomendados para expandir
    recommended_new_markets: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Multi-Market config
# ─────────────────────────────────────────────────────────────

class MarketConfig(BaseModel):
    """Configuração de mercado Tier 1."""
    language: str
    country_code: str
    name: str
    voice: str
    tts_rate: str = "+15%"
    youtube_region: str
    niche_cpm_multiplier: float = Field(default=1.0)
    google_trends_geo: str = ""


TIER1_MARKETS: dict[str, MarketConfig] = {
    "en-US": MarketConfig(
        language="en-US", country_code="US", name="United States",
        voice="en-US-AriaNeural", tts_rate="+15%",
        youtube_region="US", google_trends_geo="US",
        niche_cpm_multiplier=1.0,
    ),
    "en-GB": MarketConfig(
        language="en-GB", country_code="GB", name="United Kingdom",
        voice="en-GB-SoniaNeural", tts_rate="+10%",
        youtube_region="GB", google_trends_geo="GB",
        niche_cpm_multiplier=0.9,
    ),
    "de-DE": MarketConfig(
        language="de-DE", country_code="DE", name="Germany",
        voice="de-DE-KatjaNeural", tts_rate="+5%",
        youtube_region="DE", google_trends_geo="DE",
        niche_cpm_multiplier=0.85,
    ),
    "fr-FR": MarketConfig(
        language="fr-FR", country_code="FR", name="France",
        voice="fr-FR-DeniseNeural", tts_rate="+5%",
        youtube_region="FR", google_trends_geo="FR",
        niche_cpm_multiplier=0.8,
    ),
    "es-ES": MarketConfig(
        language="es-ES", country_code="ES", name="Spain",
        voice="es-ES-ElviraNeural", tts_rate="+10%",
        youtube_region="ES", google_trends_geo="ES",
        niche_cpm_multiplier=0.75,
    ),
}


# CPM base por nicho (USD)
NICHE_BASE_CPM: dict[str, float] = {
    "ai_tools": 28.0,
    "personal_finance": 22.0,
    "psychology_curiosity": 12.0,
    "health_longevity": 15.0,
}
