"""
Master AV Plan Models — AI Video Director (Sprint 2.4)
Modelos Pydantic para o artefato consolidado Master AudioVisual Production Plan.
Preserva compatibilidade integral com Scene V2 (shared.models.Scene).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents.video_director.models import (
    PlannedScene,
    VisualBible,
)
from shared.models import Scene


class StageExecutionMetrics(BaseModel):
    """Métricas de execução de uma etapa do pipeline do AI Video Director."""
    stage: str = Field(..., min_length=1, description="Nome da etapa")
    status: str = Field(..., min_length=1, description="Status da etapa (completed, failed)")
    duration_seconds: float = Field(..., ge=0.0, description="Duração monotônica da etapa em segundos")
    scene_count_in: int = Field(default=0, ge=0, description="Quantidade de cenas na entrada da etapa")
    scene_count_out: int = Field(default=0, ge=0, description="Quantidade de cenas na saída da etapa")
    error_type: Optional[str] = Field(default=None, description="Tipo de erro, se houver")
    error_message: Optional[str] = Field(default=None, description="Mensagem sanitizada de erro, se houver")

    model_config = ConfigDict(extra="forbid")

    @field_validator("duration_seconds")
    @classmethod
    def _validate_duration_finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v) or v < 0:
            raise ValueError(f"duration_seconds must be finite and >= 0, got {v!r}")
        return v


class ExecutionMetadata(BaseModel):
    """Metadados de observabilidade e rastreabilidade da execução do Director."""
    total_duration_seconds: float = Field(..., ge=0.0, description="Tempo total monotônico de execução")
    production_profile: str = Field(..., min_length=1, description="Perfil de produção utilizado")
    stages: List[StageExecutionMetrics] = Field(default_factory=list, description="Métricas por etapa")
    components: Dict[str, str] = Field(default_factory=dict, description="Componentes responsáveis por cada etapa")
    warnings: List[str] = Field(default_factory=list, description="Avisos não críticos documentados")

    model_config = ConfigDict(extra="forbid")

    @field_validator("total_duration_seconds")
    @classmethod
    def _validate_total_duration_finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v) or v < 0:
            raise ValueError(f"total_duration_seconds must be finite and >= 0, got {v!r}")
        return v


class MasterAVPlan(BaseModel):
    """
    Master AudioVisual Production Plan (Master AV Plan).
    Artefato único, estruturado, validado e serializável produzido pelo AIVideoDirector.
    """
    plan_id: str = Field(
        default_factory=lambda: f"avplan_{uuid.uuid4().hex[:16]}",
        min_length=1,
        description="Identificador único do plano (sem dados sensíveis)",
    )
    schema_version: str = Field(default="2.4.0", min_length=1, description="Versão explícita do contrato")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        min_length=1,
        description="Timestamp UTC ISO-8601 de criação",
    )
    production_profile: str = Field(..., min_length=1, description="Perfil de produção utilizado")
    central_theme: str = Field(..., min_length=1, description="Tema central da narrativa")
    visual_bible: VisualBible = Field(..., description="Bíblia Visual consolidada")
    total_scenes: int = Field(..., ge=1, description="Quantidade total de cenas planejadas")
    total_duration_seconds: float = Field(..., gt=0.0, description="Duração total consolidada em segundos")
    scenes: List[PlannedScene] = Field(..., min_length=1, description="Lista ordenada de cenas planejadas")
    validation_summary: Dict[str, Any] = Field(default_factory=dict, description="Resumo das validações consolidadas")
    execution_metadata: ExecutionMetadata = Field(..., description="Metadados e métricas de execução")

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("total_duration_seconds")
    @classmethod
    def _validate_total_duration_finite(cls, v: float) -> float:
        if isinstance(v, bool) or math.isnan(v) or math.isinf(v) or v <= 0:
            raise ValueError(f"total_duration_seconds must be finite and > 0, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_master_plan_integrity(self) -> "MasterAVPlan":
        if self.total_scenes != len(self.scenes):
            raise ValueError(
                f"total_scenes ({self.total_scenes}) does not match len(scenes) ({len(self.scenes)})"
            )

        seen_ids = set()
        sum_durations = 0.0
        tolerance = 1e-3 + 1e-9

        for idx, scene in enumerate(self.scenes, start=1):
            if scene.scene_id in seen_ids:
                raise ValueError(f"Duplicate scene_id in MasterAVPlan: {scene.scene_id}")
            seen_ids.add(scene.scene_id)

            if scene.order != idx:
                raise ValueError(
                    f"Scene {scene.scene_id} has order={scene.order}, expected {idx}"
                )

            if isinstance(scene.duration_seconds, bool) or math.isnan(scene.duration_seconds) or math.isinf(scene.duration_seconds):
                raise ValueError(
                    f"Scene {scene.scene_id} duration_seconds must be finite, got {scene.duration_seconds!r}"
                )

            if scene.duration_seconds <= 0:
                raise ValueError(
                    f"Scene {scene.scene_id} duration_seconds must be > 0, got {scene.duration_seconds!r}"
                )

            sum_durations += scene.duration_seconds

        if abs(sum_durations - self.total_duration_seconds) > tolerance:
            raise ValueError(
                f"Sum of scene durations ({sum_durations:.6f}) does not match "
                f"total_duration_seconds ({self.total_duration_seconds:.6f})"
            )

        return self

    def to_canonical_scenes(self) -> List[Scene]:
        """Converte todas as cenas planejadas para o contrato canônico Scene V2."""
        from agents.video_director.models import ScenePlanResult
        plan_proxy = ScenePlanResult(
            profile=self.production_profile,
            total_duration_seconds=self.total_duration_seconds,
            scene_count=self.total_scenes,
            scenes=self.scenes,
            visual_bible=self.visual_bible,
            validation_summary=self.validation_summary,
        )
        return plan_proxy.to_canonical_scenes()
