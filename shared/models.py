"""
Shared Pydantic Models — Dark Channel Factory
Objetos de dados trocados entre os agentes via filas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class NarrativePurpose(str, Enum):
    HOOK = "hook"
    CONTEXT = "context"
    TENSION = "tension"
    TECHNICAL_DETAIL = "technical_detail"
    REVELATION = "revelation"
    HISTORICAL_EVIDENCE = "historical_evidence"
    TRANSFORMATION = "transformation"
    CTA = "cta"


class VisualProductionType(str, Enum):
    AUTO = "auto"
    LOCAL_ASSET = "local_asset"
    STOCK_FOOTAGE = "stock_footage"
    CINEMATIC_STILL = "cinematic_still"
    MOTION_GRAPHIC = "motion_graphic"
    DATA_VIZ = "data_viz"
    BLENDER_3D = "blender_3d"
    AI_VIDEO = "ai_video"


class CameraFraming(str, Enum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    MEDIUM = "medium"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    MACRO = "macro"


class CameraMovement(str, Enum):
    STATIC = "static"
    SLOW_PUSH_IN = "slow_push_in"
    DRAMATIC_PULL_OUT = "dramatic_pull_out"
    TRACKING_LATERAL = "tracking_lateral"
    CRANE_SWEEP = "crane_sweep"
    ORBIT = "orbit"
    DUTCH_TILT = "dutch_tilt"
    IMPACT_MICRO_SHAKE = "impact_micro_shake"


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
# Metadados de Licença e Rastreabilidade V2
# ─────────────────────────────────────────────────────────────

class AssetSource(str, Enum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    WIKIMEDIA = "wikimedia"
    WIKIMEDIA_COMMONS = "wikimedia_commons"
    NASA = "nasa_archive"
    POLLINATIONS = "pollinations_ai"
    LOCAL_BANK = "local_bank"
    BLENDER = "blender_synthetic"
    FFMPEG_PROCEDURAL = "ffmpeg_procedural"


# Aliases canônicos de direção
ShotType = CameraFraming
VisualType = VisualProductionType


class AssetMetadata(BaseModel):
    """Metadados individuais de licenciamento e proveniência de ativo."""
    provider: str = Field(..., description="Origem ex: pexels, pixabay, wikimedia, local_bank")
    source_id_or_url: str = Field(..., description="URL canônica ou ID na plataforma")
    license_type: str = Field(..., description="Tipo de licença: cc0, pexels_license, etc.")
    license_proof_ref: str = Field(..., description="URL ou hash de evidência da licença")
    attribution_required: bool = Field(default=False)
    attribution_text: str | None = Field(default=None)
    acquired_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    local_asset_path: str = Field(...)
    sha256_hash: str = Field(..., description="Hash SHA-256 de 64 caracteres do arquivo local")
    commercial_use_approved: bool = Field(default=False, description="Aprovação estrita de uso comercial sem presunções")
    editorial_only: bool = Field(default=False)

    model_config = ConfigDict(extra="allow")

    @field_validator("sha256_hash")
    @classmethod
    def _validate_sha256_hash(cls, v: str) -> str:
        import re
        s = str(v).strip().lower()
        if not re.match(r"^[a-f0-9]{64}$", s):
            raise ValueError("sha256_hash must be a valid 64-character hexadecimal SHA-256 string")
        return s

    @model_validator(mode="after")
    def _validate_license_coherence(self) -> "AssetMetadata":
        if self.editorial_only and self.commercial_use_approved:
            raise ValueError("Asset marked as editorial_only cannot have commercial_use_approved=True")
        if self.attribution_required and (not self.attribution_text or not self.attribution_text.strip()):
            raise ValueError("attribution_text is required when attribution_required is True")
        return self


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
    """
    Contrato Canônico V2 de Cena — AI Video Director.
    Suporta intercâmbio canônico e legado com reconciliação bidirecional automática.
    """
    # Identificação e Texto (Obrigatórios)
    id: int = Field(default=1, alias="scene_id", ge=0)
    narration: str = Field(..., min_length=1, description="Texto narrado oficial (mínimo 1 palavra)")
    text: str | None = Field(default=None, description="Espelhamento de compatibilidade com consumidores legados")
    duration_s: float = Field(default=5.0, alias="estimated_duration_s", ge=1.0)
    timestamp: str | None = Field(default=None, description="Timestamp sequencial da cena ex: 0:00-0:10")

    # Classificação Narrativa
    scene_type: SceneType = Field(default=SceneType.SOLUTION)
    narrative_purpose: NarrativePurpose = Field(default=NarrativePurpose.CONTEXT)
    emotion: Emotion = Field(default=Emotion.CURIOSITY)

    # Prompts Visuais Canônicos
    b_roll_visual: str = Field(..., min_length=1, description="Prompt visual cinematográfico detalhado")
    b_roll_query: str = Field(..., min_length=1, description="Query concisa para busca de estoque")

    # Parâmetros de Direção Audiovisual
    visual_production_type: VisualProductionType = Field(default=VisualProductionType.AUTO)
    camera_framing: CameraFraming = Field(default=CameraFraming.WIDE)
    camera_movement: CameraMovement = Field(default=CameraMovement.SLOW_PUSH_IN)
    lighting_mood: str = Field(default="dark_cinematic")
    transition_in: str = Field(default="direct_cut")
    transition_out: str = Field(default="crossfade_slow")
    continuity_anchor: str | None = Field(default=None)

    # Áudio, Gráficos e Rastreabilidade
    sfx_cue: str = Field(default="none", description="Efeito sonoro sugerido")
    on_screen_text: str | None = Field(default=None, description="Texto na tela (máx 3 palavras)")
    visual_cues: list[dict[str, Any]] = Field(default_factory=list, description="Micro visual cues")
    asset_metadata: AssetMetadata | None = Field(default=None)
    estimated_cost_tier: int = Field(default=1, ge=1, le=5)

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="allow",
    )

    @property
    def scene_id(self) -> int:
        return self.id

    @property
    def estimated_duration_s(self) -> float:
        return self.duration_s

    @field_validator("narration")
    @classmethod
    def _validate_narration_not_empty(cls, v: str) -> str:
        if v is None:
            raise ValueError("Narration cannot be None")
        s = str(v)
        if not s.strip() or len(s.split()) < 1:
            raise ValueError("Narration must contain at least one non-empty word")
        return v

    @model_validator(mode="before")
    @classmethod
    def _reconcile_scene_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            import copy
            import math
            data = copy.deepcopy(data)
            from shared.scene_normalizer import extract_concise_broll_query, normalize_broll_query, CAMERA_MOVEMENT_ALIASES

            # 0. Mapeamentos legados adicionais
            if "type" in data and "scene_type" not in data:
                data["scene_type"] = data["type"]
            if "tier" in data and "estimated_cost_tier" not in data:
                data["estimated_cost_tier"] = data["tier"]
            if "overlay_text" in data and "on_screen_text" not in data:
                data["on_screen_text"] = data["overlay_text"]
            if "micro_cues" in data and "visual_cues" not in data:
                data["visual_cues"] = data["micro_cues"]
            if "anchor_id" in data and "continuity_anchor" not in data:
                data["continuity_anchor"] = data["anchor_id"]

            # 1. Reconciliação id / scene_id com detecção de conflitos
            raw_id = data.get("id")
            raw_scene_id = data.get("scene_id")
            if raw_id is not None and raw_scene_id is not None:
                try:
                    i1, i2 = int(raw_id), int(raw_scene_id)
                except (ValueError, TypeError):
                    raise ValueError(f"Scene identifiers must be valid integers: id={raw_id}, scene_id={raw_scene_id}")
                if i1 != i2:
                    raise ValueError(f"Conflicting scene identifiers provided: id={raw_id} vs scene_id={raw_scene_id}")
                final_id = i1
            elif raw_id is not None:
                final_id = int(raw_id)
            elif raw_scene_id is not None:
                final_id = int(raw_scene_id)
            else:
                final_id = 1

            if final_id < 0:
                raise ValueError(f"Scene identifier must be a non-negative integer >= 0, got {final_id}")

            data["id"] = final_id
            data["scene_id"] = final_id

            # 2. Reconciliação narration / text / content (não aceita vazio mascarando ausência)
            narr_raw = data.get("narration")
            text_raw = data.get("text")
            content_raw = data.get("content")

            chosen_raw = None
            if narr_raw is not None:
                chosen_raw = narr_raw
            elif text_raw is not None:
                chosen_raw = text_raw
            elif content_raw is not None:
                chosen_raw = content_raw

            if chosen_raw is not None:
                s_chosen = str(chosen_raw)
                if s_chosen.strip():
                    data["narration"] = s_chosen
                    data["text"] = s_chosen
                else:
                    data["narration"] = ""
                    data["text"] = ""
            else:
                data["narration"] = ""
                data["text"] = ""

            # 3. Reconciliação duration_s / estimated_duration_s / duration
            dur_s = data.get("duration_s")
            est_dur = data.get("estimated_duration_s")
            gen_dur = data.get("duration")

            if dur_s is not None and est_dur is not None:
                try:
                    d1, d2 = float(dur_s), float(est_dur)
                except (ValueError, TypeError):
                    raise ValueError(f"Duration values must be numeric: duration_s={dur_s}, estimated_duration_s={est_dur}")
                if math.isnan(d1) or math.isnan(d2) or math.isinf(d1) or math.isinf(d2):
                    raise ValueError("Duration cannot be NaN or infinite")
                if d1 <= 0.0 or d2 <= 0.0:
                    raise ValueError(f"Duration must be greater than zero: duration_s={d1}, estimated_duration_s={d2}")
                if abs(d1 - d2) > 0.01:
                    raise ValueError(f"Conflicting duration values provided: duration_s={dur_s} vs estimated_duration_s={est_dur}")
                candidate_d = d1
            elif dur_s is not None:
                candidate_d = float(dur_s)
            elif est_dur is not None:
                candidate_d = float(est_dur)
            elif gen_dur is not None:
                candidate_d = float(gen_dur)
            else:
                candidate_d = 5.0

            if math.isnan(candidate_d) or math.isinf(candidate_d):
                raise ValueError("Duration cannot be NaN or infinite")
            if candidate_d <= 0.0:
                raise ValueError(f"Duration must be greater than zero, got {candidate_d}")

            final_dur = max(1.0, candidate_d)
            data["duration_s"] = final_dur
            data["estimated_duration_s"] = final_dur

            # 4. Reconciliação b_roll_visual / b_roll_query / visual_description
            vis_canonical = data.get("b_roll_visual")
            vis_legacy = (
                data.get("visual_description")
                or data.get("visual_prompt")
                or data.get("visual")
            )
            query = data.get("b_roll_query") or data.get("query")

            is_v2_context = any(k in data for k in (
                "camera_framing", "camera_movement", "narrative_purpose",
                "visual_production_type", "asset_metadata", "lighting_mood"
            )) or bool(narr_raw and not text_raw)

            if vis_canonical and not query:
                clean_vis = str(vis_canonical).strip()
                data["b_roll_visual"] = clean_vis
                data["b_roll_query"] = extract_concise_broll_query(clean_vis)
            elif vis_legacy and not query and not vis_canonical:
                clean_leg = str(vis_legacy).strip()
                data["b_roll_visual"] = clean_leg
                data["b_roll_query"] = clean_leg
            elif query and not vis_canonical and not vis_legacy:
                clean_q = str(query).strip()
                if is_v2_context:
                    norm_q = normalize_broll_query(clean_q) if len(clean_q.split()) > 5 else clean_q
                    data["b_roll_query"] = norm_q
                    data["b_roll_visual"] = f"Cinematic visual representation based on: {norm_q}"
                else:
                    data["b_roll_query"] = clean_q
                    data["b_roll_visual"] = clean_q
            elif (vis_canonical or vis_legacy) and query:
                # Ambos fornecidos: preserva ambos os campos independentemente sem sobrescrita
                vis = vis_canonical or vis_legacy
                data["b_roll_visual"] = str(vis).strip()
                data["b_roll_query"] = str(query).strip()

            # 5. Normalização de case e aliases para enums
            for enum_field in (
                "camera_framing", "camera_movement", "narrative_purpose",
                "visual_production_type", "scene_type", "emotion"
            ):
                val = data.get(enum_field)
                if hasattr(val, "value"):
                    val = val.value
                if isinstance(val, str):
                    clean_v = val.strip().lower()
                    if enum_field == "camera_movement":
                        clean_v = CAMERA_MOVEMENT_ALIASES.get(clean_v, clean_v)
                    elif enum_field == "emotion" and clean_v in {"authority", "confidence"}:
                        clean_v = "trust"
                    data[enum_field] = clean_v

        return data


class ScriptData(BaseModel):
    """Output do Copywriter Agent — input dos Agents 3 e 4."""
    job_id: UUID
    source_job_id: UUID | None = Field(
        default=None,
        description="ID do roteiro matriz usado para compartilhar os mesmos B-rolls entre idiomas",
    )
    target_channel_id: str | None = Field(default=None)
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
