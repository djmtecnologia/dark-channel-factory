"""
Models for AI Video Director — Narrative Analyzer and Visual Director.
Contrato tipado e estruturado para interpretação dramática e direção visual.
"""
from __future__ import annotations

import copy
from enum import Enum
import math
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.models import (
    CameraFraming,
    CameraMovement,
    Emotion,
    NarrativePurpose,
    Scene,
    SceneType,
    VisualProductionType,
)


class VisualLanguage(str, Enum):
    """Linguagem e atmosfera visual dominante."""
    DOCUMENTARY_REALISTIC = "documentary_realistic"
    CINEMATIC = "cinematic"
    HISTORICAL = "historical"
    INVESTIGATIVE = "investigative"
    DRAMATIC = "dramatic"
    CONTEMPLATIVE = "contemplative"
    TENSE = "tense"


class VisualImpactPotential(str, Enum):
    """Potencial de impacto visual da tomada."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class EvidenceType(str, Enum):
    """Tipo de evidência documental ou factual necessária."""
    DOCUMENT = "document"
    MAP = "map"
    PHOTOGRAPH = "photograph"
    PERSON = "person"
    LOCATION = "location"
    OBJECT = "object"
    DATA_METRIC = "data_metric"
    TELEMETRY = "telemetry"


# ─────────────────────────────────────────────────────────────
# Modelos do NarrativeAnalyzer
# ─────────────────────────────────────────────────────────────

class SceneNarrativeInterpretation(BaseModel):
    """Interpretação narrativa estruturada de uma cena individual."""
    scene_id: int = Field(..., ge=0, description="Identificador único da cena correspondente")
    original_narration: str = Field(..., min_length=1, description="Texto original intacto da narração")
    scene_intent: str = Field(..., min_length=1, description="Intenção e propósito dramático da cena")
    narrative_purpose: NarrativePurpose = Field(default=NarrativePurpose.CONTEXT)
    predominant_emotion: Emotion = Field(default=Emotion.CURIOSITY)
    emotional_intensity: float = Field(default=0.5, ge=0.0, le=1.0, description="Intensidade emocional de 0.0 a 1.0")
    narrative_tension: float = Field(default=0.5, ge=0.0, le=1.0, description="Tensão dramática de 0.0 a 1.0")
    dramatic_importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Peso dramático no arco narrativo de 0.0 a 1.0")
    
    # Diferenciação epistêmica estrita
    factual_assertions: List[str] = Field(default_factory=list, description="O que o roteiro afirma explicitamente como fato")
    narrative_inferences: List[str] = Field(default_factory=list, description="O que pode ser inferido narrativamente sem ser fato histórico")
    visual_proof_requirements: List[str] = Field(default_factory=list, description="Informações que precisam de comprovação visual documental")
    forbidden_fabrications: List[str] = Field(default_factory=list, description="O que NÃO deve ser inventado ou presumido visualmente")
    
    # Necessidades audiovisuais
    required_visual_elements: List[str] = Field(default_factory=list, description="Elementos indispensáveis para a compreensão do espectador")
    evidence_needed: List[EvidenceType] = Field(default_factory=list, description="Categorias de evidência documental necessárias")
    visual_impact_potential: VisualImpactPotential = Field(default=VisualImpactPotential.MEDIUM)

    # Campos preservados de contexto original para conversão canônica V2
    duration_s: Optional[float] = Field(default=None, ge=1.0, description="Duração original preservada da cena")
    timestamp: Optional[str] = Field(default=None, description="Timestamp sequencial preservado")
    scene_type: Optional[SceneType] = Field(default=None, description="Tipo canônico de cena original")
    extra_fields: Dict[str, Any] = Field(default_factory=dict, description="Metadados adicionais preservados da entrada")

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("original_narration")
    @classmethod
    def _validate_original_narration_not_empty(cls, v: str) -> str:
        # Preservação exata: rejeita se vazio ou apenas espaços, mas NÃO aplica .strip()
        if not v or not str(v).strip():
            raise ValueError("original_narration cannot be empty")
        return v

    @field_validator("evidence_needed", mode="before")
    @classmethod
    def _validate_evidence_needed(cls, v: Any) -> List[EvidenceType]:
        if not v:
            return []
        if not isinstance(v, list):
            raise ValueError("evidence_needed must be a list of EvidenceType categories")
        
        EVIDENCE_ALIASES: Dict[str, EvidenceType] = {
            "photo": EvidenceType.PHOTOGRAPH,
            "image": EvidenceType.PHOTOGRAPH,
            "foto": EvidenceType.PHOTOGRAPH,
            "fotografia": EvidenceType.PHOTOGRAPH,
            "chart": EvidenceType.DATA_METRIC,
            "metrics": EvidenceType.DATA_METRIC,
            "metric": EvidenceType.DATA_METRIC,
            "doc": EvidenceType.DOCUMENT,
            "documento": EvidenceType.DOCUMENT,
            "archive": EvidenceType.DOCUMENT,
            "place": EvidenceType.LOCATION,
            "local": EvidenceType.LOCATION,
            "satellite": EvidenceType.TELEMETRY,
            "telemetria": EvidenceType.TELEMETRY,
            "objeto": EvidenceType.OBJECT,
            "pessoa": EvidenceType.PERSON,
            "mapa": EvidenceType.MAP,
        }
        validated: List[EvidenceType] = []
        for item in v:
            if isinstance(item, EvidenceType):
                validated.append(item)
                continue
            if not isinstance(item, str) or not item.strip():
                raise ValueError("evidence_needed category cannot be empty or non-string")
            clean_item = item.strip().lower()
            if clean_item in EVIDENCE_ALIASES:
                validated.append(EVIDENCE_ALIASES[clean_item])
            else:
                try:
                    validated.append(EvidenceType(clean_item))
                except ValueError:
                    allowed = [e.value for e in EvidenceType]
                    raise ValueError(f"Invalid evidence category '{clean_item}'. Allowed categories: {allowed}")
        return validated

    @model_validator(mode="after")
    def _validate_epistemic_separation(self) -> "SceneNarrativeInterpretation":
        """Garante a separação epistêmica estrita entre fatos e inferências narrativas."""
        facts_clean = {f.strip().lower() for f in self.factual_assertions if isinstance(f, str) and f.strip()}
        inferences_clean = {inf.strip().lower() for inf in self.narrative_inferences if isinstance(inf, str) and inf.strip()}
        
        overlap = facts_clean.intersection(inferences_clean)
        if overlap:
            raise ValueError(
                f"Epistemic violation: narrative inference cannot be claimed as factual assertion: {overlap}"
            )
        return self


class NarrativeAnalysisResult(BaseModel):
    """Resultado global da interpretação narrativa de um roteiro."""
    central_theme: str = Field(..., min_length=1, description="Tema central do roteiro")
    historical_or_factual_context: str = Field(..., min_length=1, description="Contexto factual ou histórico delimitado")
    narrative_objective: str = Field(..., min_length=1, description="Objetivo narrativo principal da narrativa")
    narrative_progression: List[str] = Field(..., description="Etapas da progressão narrativa (entre 3 e 6 etapas)")
    dominant_tone: str = Field(default="documentary_investigative", description="Tom narrativo predominante")
    scene_analyses: List[SceneNarrativeInterpretation] = Field(default_factory=list)
    raw_word_count: int = Field(default=0, ge=0)
    total_scenes: int = Field(default=0, ge=0)

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("narrative_progression")
    @classmethod
    def _validate_narrative_progression(cls, v: List[str]) -> List[str]:
        if not isinstance(v, list):
            raise ValueError("narrative_progression must be a list")
        if len(v) < 3 or len(v) > 6:
            raise ValueError(f"narrative_progression must contain between 3 and 6 steps, got {len(v)}")
        
        cleaned: List[str] = []
        seen = set()
        for idx, step in enumerate(v):
            if not isinstance(step, str) or not step.strip():
                raise ValueError(f"narrative_progression step {idx} cannot be empty or whitespace-only")
            s = step.strip()
            s_lower = s.lower()
            if s_lower in seen:
                raise ValueError(f"narrative_progression contains duplicate step: '{s}'")
            seen.add(s_lower)
            cleaned.append(s)
        return cleaned

    @model_validator(mode="after")
    def _validate_scene_count(self) -> "NarrativeAnalysisResult":
        if "total_scenes" in self.model_fields_set:
            if self.total_scenes != len(self.scene_analyses):
                raise ValueError(
                    f"total_scenes ({self.total_scenes}) does not match "
                    f"len(scene_analyses) ({len(self.scene_analyses)})"
                )
        else:
            self.total_scenes = len(self.scene_analyses)
        return self


# ─────────────────────────────────────────────────────────────
# Modelos do VisualDirector
# ─────────────────────────────────────────────────────────────

class VisualBible(BaseModel):
    """Diretrizes estéticas e conceituais globais do vídeo."""
    visual_language: VisualLanguage = Field(..., description="Linguagem visual dominante")
    dominant_style: str = Field(..., min_length=1, description="Estilo visual predominante")
    color_palette: List[str] = Field(
        ...,
        min_length=1,
        description="Paleta visual de cores primárias e de destaque"
    )
    lighting_mood: str = Field(..., min_length=1, description="Atmosfera de iluminação global")
    pacing_rhythm: str = Field(..., min_length=1, description="Ritmo visual predominante")

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    @field_validator("dominant_style", "lighting_mood", "pacing_rhythm")
    @classmethod
    def _validate_non_empty_strings(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field cannot be empty or whitespace-only")
        return v

    @field_validator("color_palette")
    @classmethod
    def _validate_color_palette(cls, v: List[str]) -> List[str]:
        if not v or not isinstance(v, list):
            raise ValueError("color_palette cannot be empty")
        for c in v:
            if not isinstance(c, str) or not c.strip():
                raise ValueError("color_palette items must be non-empty strings")
        return v


class SceneVisualDirection(BaseModel):
    """Instruções detalhadas de direção cinematográfica por cena."""
    scene_id: int = Field(..., ge=0)
    narration: str = Field(..., min_length=1, description="Texto original intacto da narração")
    what_viewer_sees: str = Field(..., min_length=1, description="O que o espectador precisa enxergar na tela")
    dominant_element: str = Field(..., min_length=1, description="Elemento que domina a composição visual")
    visual_production_type: VisualProductionType = Field(default=VisualProductionType.AUTO)
    camera_framing: CameraFraming = Field(default=CameraFraming.WIDE)
    camera_movement: CameraMovement = Field(default=CameraMovement.SLOW_PUSH_IN)
    depth_of_field: str = Field(default="deep_focus", description="Profundidade de campo planejada")
    composition: str = Field(default="rule_of_thirds", description="Regra de composição e equilíbrio visual")
    lighting_mood: str = Field(default="dark_cinematic")
    atmosphere: str = Field(default="solemn_tension")
    color_palette_notes: str = Field(default="desaturated cold tones with selective amber accents")
    transition_in: str = Field(default="direct_cut")
    transition_out: str = Field(default="crossfade_slow")
    visual_connection_previous: Optional[str] = Field(default=None, description="Conexão com a cena anterior")
    visual_connection_next: Optional[str] = Field(default=None, description="Conexão com a próxima cena")
    continuity_anchor: Optional[str] = Field(default=None, description="Elemento de continuidade a manter")
    
    # Prompts de execução compatíveis com o Contrato V2
    b_roll_visual: str = Field(..., min_length=1, description="Prompt visual cinematográfico detalhado em inglês")
    b_roll_query: str = Field(..., min_length=1, description="Query concisa de 2 a 5 palavras para APIs de estoque")
    sfx_cue: str = Field(default="none")
    on_screen_text: Optional[str] = Field(default=None)
    estimated_cost_tier: int = Field(default=1, ge=1, le=5)

    # Campos preservados para conversão canônica V2
    duration_s: Optional[float] = Field(default=None, ge=1.0)
    timestamp: Optional[str] = Field(default=None)
    narrative_purpose: Optional[NarrativePurpose] = Field(default=None)
    emotion: Optional[Emotion] = Field(default=None)
    scene_type: Optional[SceneType] = Field(default=None)
    extra_fields: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("narration")
    @classmethod
    def _validate_narration_not_empty(cls, v: str) -> str:
        # Preservação exata: rejeita se vazio ou apenas espaços, mas NÃO aplica .strip()
        if not v or not str(v).strip():
            raise ValueError("narration cannot be empty")
        return v

    @field_validator("b_roll_query")
    @classmethod
    def _validate_b_roll_query(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("b_roll_query cannot be empty")
        
        # Remove espaços excedentes APENAS da query de busca (nunca da narração)
        cleaned = " ".join(v.strip().split())
        words = cleaned.split()
        if len(words) < 2 or len(words) > 5:
            raise ValueError(
                f"b_roll_query must contain between 2 and 5 words, got {len(words)}: '{cleaned}'"
            )
        
        # Garante que cada palavra contenha caracteres alfanuméricos válidos
        for w in words:
            if not re.search(r"\w", w):
                raise ValueError(f"b_roll_query contains invalid word token: '{w}'")
        return cleaned

    @field_validator("on_screen_text")
    @classmethod
    def _validate_on_screen_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("on_screen_text must be a string or None")
        
        s = v.strip()
        if not s:
            raise ValueError("on_screen_text cannot be empty string; use None if absent")
        
        # Validação estrita do limite de 3 palavras (sem truncamento silencioso)
        words = s.split()
        if len(words) > 3:
            raise ValueError(
                f"on_screen_text exceeds maximum limit of 3 words (got {len(words)}: '{s}')"
            )
        return s


class VisualDirectionResult(BaseModel):
    """Plano de direção visual completo consolidado."""
    visual_bible: VisualBible = Field(..., description="Diretrizes estéticas globais do vídeo")
    scene_directions: List[SceneVisualDirection] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    def to_canonical_scenes(self, original_scenes: Optional[List[Any]] = None) -> List[Scene]:
        """
        Converte as direções visuais diretamente em instâncias do modelo canônico Scene (V2).
        Preserva rigorosamente a narração original, durações, timestamps, conexões narrativas
        e metadados contextuais sem substituição silenciosa por defaults.
        """
        orig_map: Dict[int, Any] = {}
        if original_scenes:
            for s in original_scenes:
                sid = None
                if isinstance(s, dict):
                    if "id" in s and s["id"] is not None:
                        sid = s["id"]
                    elif "scene_id" in s and s["scene_id"] is not None:
                        sid = s["scene_id"]
                else:
                    sid = getattr(s, "id", None)
                    if sid is None:
                        sid = getattr(s, "scene_id", None)
                if sid is not None:
                    try:
                        orig_map[int(sid)] = s
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f"Invalid original scene id: {sid!r}") from exc

        scenes: List[Scene] = []
        for d in self.scene_directions:
            scene_dict: Dict[str, Any] = {
                "id": d.scene_id,
                "narration": d.narration,
                "b_roll_visual": d.b_roll_visual,
                "b_roll_query": d.b_roll_query,
                "visual_production_type": d.visual_production_type,
                "camera_framing": d.camera_framing,
                "camera_movement": d.camera_movement,
                "lighting_mood": d.lighting_mood,
                "transition_in": d.transition_in,
                "transition_out": d.transition_out,
                "continuity_anchor": d.continuity_anchor,
                "sfx_cue": d.sfx_cue,
                "on_screen_text": d.on_screen_text,
                "estimated_cost_tier": d.estimated_cost_tier,
            }

            # Preserva duração original quando existir (sem usar `or` para não confundir 0 com None)
            orig_s = orig_map.get(d.scene_id)
            duration = None
            if d.duration_s is not None:
                duration = d.duration_s
            elif orig_s is not None:
                if isinstance(orig_s, dict):
                    for key in ("duration_s", "estimated_duration_s", "duration"):
                        if key in orig_s and orig_s[key] is not None:
                            duration = orig_s[key]
                            break
                else:
                    for attr in ("duration_s", "estimated_duration_s", "duration"):
                        val = getattr(orig_s, attr, None)
                        if val is not None:
                            duration = val
                            break

            if duration is not None:
                try:
                    parsed_duration = float(duration)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Invalid duration value for scene {d.scene_id}: {duration!r}") from exc
                if parsed_duration <= 0 or parsed_duration != parsed_duration:  # <= 0 or NaN
                    raise ValueError(f"Invalid duration value for scene {d.scene_id}: {duration!r}")
                scene_dict["duration_s"] = parsed_duration
            else:
                scene_dict["duration_s"] = 5.0

            # Preserva estimated_duration_s se existir na origem
            if orig_s is not None:
                est_dur = orig_s.get("estimated_duration_s") if isinstance(orig_s, dict) else getattr(orig_s, "estimated_duration_s", None)
                if est_dur is not None:
                    scene_dict["estimated_duration_s"] = est_dur

            # Preserva timestamp sequencial quando existir (inclusive 0)
            ts = d.timestamp
            if ts is None and orig_s is not None:
                ts = orig_s.get("timestamp") if isinstance(orig_s, dict) else getattr(orig_s, "timestamp", None)
            if ts is not None:
                scene_dict["timestamp"] = str(ts)

            # Preserva classificação narrativa
            if d.narrative_purpose is not None:
                scene_dict["narrative_purpose"] = d.narrative_purpose
            elif orig_s is not None:
                np = orig_s.get("narrative_purpose") if isinstance(orig_s, dict) else getattr(orig_s, "narrative_purpose", None)
                if np is not None:
                    scene_dict["narrative_purpose"] = np

            if d.emotion is not None:
                scene_dict["emotion"] = d.emotion
            elif orig_s is not None:
                em = orig_s.get("emotion") if isinstance(orig_s, dict) else getattr(orig_s, "emotion", None)
                if em is not None:
                    scene_dict["emotion"] = em

            if d.scene_type is not None:
                scene_dict["scene_type"] = d.scene_type
            elif orig_s is not None:
                st = orig_s.get("scene_type") if isinstance(orig_s, dict) else getattr(orig_s, "scene_type", None)
                if st is not None:
                    scene_dict["scene_type"] = st

            # Preserva metadados customizados
            if d.extra_fields:
                scene_dict.update(d.extra_fields)
            if orig_s is not None:
                if isinstance(orig_s, dict):
                    for k, v in orig_s.items():
                        if k not in scene_dict and k not in {"id", "narration", "text"}:
                            scene_dict[k] = v
                else:
                    orig_extra = getattr(orig_s, "model_extra", None)
                    if isinstance(orig_extra, dict):
                        for k, v in orig_extra.items():
                            if k not in scene_dict and k not in {"id", "narration", "text"}:
                                scene_dict[k] = v

            scenes.append(Scene(**scene_dict))
        return scenes


# ─────────────────────────────────────────────────────────────
# Modelos do ScenePlanner (Sprint 2.3)
# ─────────────────────────────────────────────────────────────

class PlannedScene(BaseModel):
    """Cena planejada pelo ScenePlanner, compatível com o Contrato Canônico V2."""
    scene_id: int = Field(..., ge=0, description="Identificador único da cena (preserva ID 0)")
    order: int = Field(..., ge=1, description="Índice sequencial da cena (1..N)")
    narration: str = Field(..., min_length=1, description="Texto original intacto da narração")
    narrative_role: str = Field(..., min_length=1, description="Função narrativa na progressão documental")
    narrative_purpose: NarrativePurpose = Field(default=NarrativePurpose.CONTEXT)
    visual_intent: str = Field(..., min_length=1, description="Intenção visual derivada da direção visual")
    duration_seconds: float = Field(..., gt=0.0, description="Duração planejada em segundos (> 0)")
    start_time: float = Field(..., ge=0.0, description="Timestamp de início em segundos")
    end_time: float = Field(..., gt=0.0, description="Timestamp de término em segundos")
    timestamp: str = Field(..., min_length=1, description="Timestamp formatado ex: 0:00-0:10")
    visual_direction: SceneVisualDirection = Field(..., description="Direção visual associada à cena")
    transition_in: str = Field(default="direct_cut", min_length=1)
    transition_out: str = Field(default="crossfade_slow", min_length=1)
    transition_rationale: str = Field(default="", description="Justificativa narrativa ou visual da transição")
    continuity_notes: Dict[str, Any] = Field(default_factory=dict, description="Metadados de continuidade visual/espacial/temporal")
    planning_rationale: str = Field(default="", description="Justificativa de planejamento da cena")
    extra_fields: Dict[str, Any] = Field(default_factory=dict, description="Metadados adicionais preservados")

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("narration")
    @classmethod
    def _validate_narration_verbatim(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("narration cannot be empty")
        return v

    @field_validator("duration_seconds", "start_time", "end_time")
    @classmethod
    def _validate_finite_numbers(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"Numeric time value must be finite, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_time_consistency(self) -> "PlannedScene":
        if self.end_time <= self.start_time:
            raise ValueError(
                f"end_time ({self.end_time}) must be greater than start_time ({self.start_time})"
            )
        return self


class ScenePlanResult(BaseModel):
    """Resultado consolidado do ScenePlanner, pronto para o Master AV Plan."""
    profile: str = Field(..., min_length=1, description="Perfil de produção utilizado")
    total_duration_seconds: float = Field(..., gt=0.0, description="Soma exata das durações planejadas")
    scene_count: int = Field(..., ge=1, description="Quantidade total de cenas planejadas")
    scenes: List[PlannedScene] = Field(..., min_length=1, description="Lista ordenada de cenas planejadas")
    visual_bible: VisualBible = Field(..., description="Bíblia Visual aplicada ao planejamento")
    validation_summary: Dict[str, Any] = Field(default_factory=dict, description="Resumo das validações determinísticas")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadados para integração com Master AV Plan")

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("total_duration_seconds")
    @classmethod
    def _validate_total_duration_finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v) or v <= 0:
            raise ValueError(f"total_duration_seconds must be finite and > 0, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_plan_integrity(self) -> "ScenePlanResult":
        if self.scene_count != len(self.scenes):
            raise ValueError(
                f"scene_count ({self.scene_count}) does not match len(scenes) ({len(self.scenes)})"
            )

        seen_ids = set()
        expected_start = 0.0
        sum_durations = 0.0
        tolerance = 1e-3 + 1e-9

        for idx, scene in enumerate(self.scenes, start=1):
            if scene.scene_id in seen_ids:
                raise ValueError(f"Duplicate scene_id in ScenePlanResult: {scene.scene_id}")
            seen_ids.add(scene.scene_id)

            if scene.order != idx:
                raise ValueError(
                    f"Scene {scene.scene_id} has order={scene.order}, expected {idx}"
                )

            for field_name, val in (
                ("start_time", scene.start_time),
                ("end_time", scene.end_time),
                ("duration_seconds", scene.duration_seconds),
            ):
                if math.isnan(val) or math.isinf(val):
                    raise ValueError(
                        f"Scene {scene.scene_id} {field_name} must be finite, got {val!r}"
                    )

            if scene.duration_seconds < 1.0:
                raise ValueError(
                    f"Scene {scene.scene_id} duration_seconds must be >= 1.0, got {scene.duration_seconds}"
                )

            if scene.end_time <= scene.start_time:
                raise ValueError(
                    f"Scene {scene.scene_id} end_time ({scene.end_time}) must be > start_time ({scene.start_time})"
                )

            if idx == 1 and abs(scene.start_time - 0.0) > 1e-6:
                raise ValueError(
                    f"First scene must start at 0.0, got {scene.start_time}"
                )

            if abs(scene.start_time - expected_start) > tolerance:
                raise ValueError(
                    f"Scene {scene.scene_id} start_time ({scene.start_time}) creates gap or overlap "
                    f"with previous end_time ({expected_start})"
                )

            span = scene.end_time - scene.start_time
            if abs(scene.duration_seconds - span) > tolerance:
                raise ValueError(
                    f"Scene {scene.scene_id} duration_seconds ({scene.duration_seconds}) does not match "
                    f"end_time - start_time ({span:.6f})"
                )

            expected_start = scene.end_time
            sum_durations += scene.duration_seconds

        if abs(self.total_duration_seconds - sum_durations) > max(tolerance, len(self.scenes) * 1e-3):
            raise ValueError(
                f"total_duration_seconds ({self.total_duration_seconds}) does not match "
                f"sum of scene durations ({sum_durations:.6f})"
            )

        return self

    def to_canonical_scenes(self) -> List[Scene]:
        """
        Converte as cenas planejadas em instâncias do contrato canônico Scene (V2),
        preservando narração verbatim, durações, timestamps e metadados.
        """
        canonical: List[Scene] = []
        for ps in self.scenes:
            vd = ps.visual_direction
            scene_dict: Dict[str, Any] = {
                "id": ps.scene_id,
                "narration": ps.narration,
                "duration_s": ps.duration_seconds,
                "timestamp": ps.timestamp,
                "narrative_purpose": ps.narrative_purpose,
                "emotion": vd.emotion if vd.emotion is not None else "curiosity",
                "scene_type": vd.scene_type if vd.scene_type is not None else "solution",
                "b_roll_visual": vd.b_roll_visual,
                "b_roll_query": vd.b_roll_query,
                "visual_production_type": vd.visual_production_type,
                "camera_framing": vd.camera_framing,
                "camera_movement": vd.camera_movement,
                "lighting_mood": vd.lighting_mood,
                "transition_in": ps.transition_in,
                "transition_out": ps.transition_out,
                "continuity_anchor": vd.continuity_anchor,
                "sfx_cue": vd.sfx_cue,
                "on_screen_text": vd.on_screen_text,
                "estimated_cost_tier": vd.estimated_cost_tier,
            }
            if ps.extra_fields:
                for k, v in ps.extra_fields.items():
                    if k not in scene_dict and k not in {"id", "narration", "text"}:
                        scene_dict[k] = v
            canonical.append(Scene(**scene_dict))
        return canonical
