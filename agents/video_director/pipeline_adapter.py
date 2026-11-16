"""
MasterPlanAdapter — Sprint 2.5 (Tarefa 1)
Converte MasterAVPlan validado para o contrato operacional do Pipeline B,
garantindo preservação literal de narração, identificadores, ordem e duração,
além de preservação integral de metadados sem consumidor atual.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.video_director.master_plan_models import MasterAVPlan
from agents.video_director.models import PlannedScene
from shared.models import Scene


class MasterPlanAdapterError(Exception):
    """Erro base do MasterPlanAdapter."""


class MasterPlanDataLossError(MasterPlanAdapterError):
    """Levantada quando há perda de informação, divergência de contagem ou integridade."""


@dataclass(frozen=True)
class AdaptedPipelineBPayload:
    """
    Resultado da conversão do MasterAVPlan para o Pipeline B.
    Nota: frozen=True impede reatribuição de atributos da instância; o Adapter
    não modifica o objeto de origem (MasterAVPlan) e retorna estruturas
    independentes (via copy.deepcopy) para listas e dicionários internos.
    """
    scenes: List[Dict[str, Any]]
    director_metadata: Dict[str, Any]
    master_av_plan_summary: Dict[str, Any]
    preserved_fields_report: Dict[str, Any] = field(default_factory=dict)


MAPPED_TO_PIPELINE_B_FIELDS = [
    "id", "scene_id", "order", "narration", "text", "duration_s",
    "duration_seconds", "timestamp", "narrative_purpose", "emotion",
    "scene_type", "b_roll_visual", "b_roll_query", "visual_production_type",
    "camera_framing", "camera_movement", "lighting_mood", "transition_in",
    "transition_out", "continuity_anchor", "sfx_cue", "on_screen_text",
    "estimated_cost_tier", "visual_cues", "micro_cues",
]

EFFECTIVELY_CONSUMED_BY_PIPELINE_B = [
    "narration", "id", "scene_id", "order", "duration_s",
    "b_roll_visual", "b_roll_query", "sfx_cue", "visual_cues", "micro_cues",
]

PRESERVED_WITHOUT_CONSUMER = [
    "visual_bible", "execution_metadata", "validation_summary",
    "narrative_role", "visual_intent", "start_time", "end_time",
    "transition_rationale", "continuity_notes", "planning_rationale",
    "visual_direction_full", "extra_fields",
]

TRANSFORMED_BY_NORMALIZER = {
    "narration": "Adapter preserva byte a byte; normalize_scenes() aplica .strip() nas extremidades sem alterar o conteúdo interno.",
}


class MasterPlanAdapter:
    """
    Adapter determinístico entre MasterAVPlan (Arquitetura A / Video Director)
    e o contrato operacional de cenas do Pipeline B (compatível com normalize_scenes).
    """

    def to_pipeline_b(self, master_plan: MasterAVPlan) -> AdaptedPipelineBPayload:
        """
        Converte um MasterAVPlan validado para o formato operacional do Pipeline B
        sem mutar o objeto de origem e validando integridade anti-perda.
        """
        if not isinstance(master_plan, MasterAVPlan):
            raise MasterPlanAdapterError(
                f"Expected MasterAVPlan instance, got {type(master_plan).__name__}"
            )

        if master_plan.total_scenes != len(master_plan.scenes):
            raise MasterPlanDataLossError(
                f"Scene count mismatch in MasterAVPlan: total_scenes={master_plan.total_scenes}, "
                f"len(scenes)={len(master_plan.scenes)}"
            )

        canonical_scenes: List[Scene] = master_plan.to_canonical_scenes()
        if len(canonical_scenes) != master_plan.total_scenes:
            raise MasterPlanDataLossError(
                f"Canonical scene count mismatch: expected {master_plan.total_scenes}, "
                f"got {len(canonical_scenes)}"
            )

        operational_scenes: List[Dict[str, Any]] = []
        scenes_unconsumed: Dict[str, Dict[str, Any]] = {}

        for idx, (planned, canonical) in enumerate(
            zip(master_plan.scenes, canonical_scenes), start=1
        ):
            op_scene, unconsumed = self._convert_scene(planned, canonical, expected_order=idx)
            operational_scenes.append(op_scene)
            scenes_unconsumed[str(planned.scene_id)] = unconsumed

        self._validate_output_integrity(master_plan, operational_scenes)

        director_metadata: Dict[str, Any] = {
            "plan_id": master_plan.plan_id,
            "schema_version": master_plan.schema_version,
            "production_profile": master_plan.production_profile,
            "central_theme": master_plan.central_theme,
            "visual_bible": copy.deepcopy(master_plan.visual_bible.model_dump()),
            "validation_summary": copy.deepcopy(master_plan.validation_summary),
            "execution_metadata": copy.deepcopy(master_plan.execution_metadata.model_dump()),
            "scenes_unconsumed": scenes_unconsumed,
        }

        master_av_plan_summary: Dict[str, Any] = {
            "plan_id": master_plan.plan_id,
            "schema_version": master_plan.schema_version,
            "created_at": master_plan.created_at,
            "production_profile": master_plan.production_profile,
            "total_scenes": master_plan.total_scenes,
            "total_duration_seconds": master_plan.total_duration_seconds,
        }

        integrity_checks = self._verify_anti_loss_report(
            master_plan,
            operational_scenes,
            director_metadata,
            canonical_scenes=canonical_scenes,
            master_av_plan_summary=master_av_plan_summary,
        )

        preserved_fields_report = {
            "total_scenes_converted": len(operational_scenes),
            "mapped_to_pipeline_b": list(MAPPED_TO_PIPELINE_B_FIELDS),
            "effectively_consumed_by_pipeline_b": list(EFFECTIVELY_CONSUMED_BY_PIPELINE_B),
            "preserved_without_consumer": list(PRESERVED_WITHOUT_CONSUMER),
            "transformed_by_normalizer": dict(TRANSFORMED_BY_NORMALIZER),
            "integrity_checks": integrity_checks,
        }

        return AdaptedPipelineBPayload(
            scenes=operational_scenes,
            director_metadata=director_metadata,
            master_av_plan_summary=master_av_plan_summary,
            preserved_fields_report=preserved_fields_report,
        )

    def _convert_scene(
        self,
        planned: PlannedScene,
        canonical: Scene,
        expected_order: int,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Converte uma PlannedScene + Scene V2 para dict operacional e metadados preservados."""
        vd = planned.visual_direction
        canonical_dump = canonical.model_dump()

        visual_cues = copy.deepcopy(
            planned.extra_fields.get("visual_cues")
            or planned.continuity_notes.get("visual_cues")
        )
        micro_cues = copy.deepcopy(
            planned.extra_fields.get("micro_cues")
            or planned.continuity_notes.get("micro_cues")
        )

        op_scene: Dict[str, Any] = {
            "id": planned.scene_id,
            "scene_id": planned.scene_id,
            "order": planned.order,
            "narration": planned.narration,
            "text": planned.narration,
            "duration_s": planned.duration_seconds,
            "duration_seconds": planned.duration_seconds,
            "timestamp": planned.timestamp,
            "narrative_purpose": canonical_dump.get("narrative_purpose", planned.narrative_purpose),
            "emotion": canonical_dump.get("emotion"),
            "scene_type": canonical_dump.get("scene_type"),
            "b_roll_visual": canonical_dump.get("b_roll_visual") or vd.what_viewer_sees,
            "b_roll_query": canonical_dump.get("b_roll_query"),
            "visual_production_type": canonical_dump.get("visual_production_type", vd.visual_production_type),
            "camera_framing": canonical_dump.get("camera_framing", vd.camera_framing),
            "camera_movement": canonical_dump.get("camera_movement", vd.camera_movement),
            "lighting_mood": canonical_dump.get("lighting_mood", vd.lighting_mood),
            "transition_in": planned.transition_in,
            "transition_out": planned.transition_out,
            "continuity_anchor": canonical_dump.get("continuity_anchor"),
            "sfx_cue": canonical_dump.get("sfx_cue"),
            "on_screen_text": canonical_dump.get("on_screen_text"),
            "estimated_cost_tier": canonical_dump.get("estimated_cost_tier"),
        }

        if visual_cues is not None:
            op_scene["visual_cues"] = visual_cues
        if micro_cues is not None:
            op_scene["micro_cues"] = micro_cues

        # Preservação integral de SceneVisualDirection e metadados sem consumidor atual
        unconsumed: Dict[str, Any] = {
            "scene_id": planned.scene_id,
            "narrative_role": planned.narrative_role,
            "visual_intent": planned.visual_intent,
            "start_time": planned.start_time,
            "end_time": planned.end_time,
            "transition_rationale": planned.transition_rationale,
            "continuity_notes": copy.deepcopy(planned.continuity_notes),
            "planning_rationale": planned.planning_rationale,
            "visual_direction_full": copy.deepcopy(vd.model_dump()),
            "planned_scene_full": copy.deepcopy(planned.model_dump()),
            "extra_fields": copy.deepcopy(planned.extra_fields),
        }

        if planned.order != expected_order:
            raise MasterPlanDataLossError(
                f"Scene order mismatch for scene_id={planned.scene_id}: "
                f"got {planned.order}, expected {expected_order}"
            )

        return op_scene, unconsumed

    def _validate_output_integrity(
        self,
        master_plan: MasterAVPlan,
        operational_scenes: List[Dict[str, Any]],
    ) -> None:
        """Valida que não houve perda de contagem, IDs, ordem, duração ou narração."""
        if len(operational_scenes) != master_plan.total_scenes:
            raise MasterPlanDataLossError(
                f"Output scene count ({len(operational_scenes)}) != "
                f"master_plan.total_scenes ({master_plan.total_scenes})"
            )

        for idx, (planned, op) in enumerate(zip(master_plan.scenes, operational_scenes), start=1):
            if op.get("scene_id") != planned.scene_id or op.get("id") != planned.scene_id:
                raise MasterPlanDataLossError(
                    f"scene_id mismatch at index {idx}: expected {planned.scene_id}, "
                    f"got id={op.get('id')}, scene_id={op.get('scene_id')}"
                )
            if op.get("order") != planned.order or op.get("order") != idx:
                raise MasterPlanDataLossError(
                    f"order mismatch for scene_id={planned.scene_id}: "
                    f"expected {planned.order}, got {op.get('order')}"
                )
            if op.get("narration") != planned.narration:
                raise MasterPlanDataLossError(
                    f"Narration integrity violation for scene_id={planned.scene_id}"
                )
            dur = op.get("duration_s")
            if (
                dur is None
                or isinstance(dur, bool)
                or math.isnan(dur)
                or math.isinf(dur)
                or abs(dur - planned.duration_seconds) > 1e-9
            ):
                raise MasterPlanDataLossError(
                    f"Duration mismatch for scene_id={planned.scene_id}: "
                    f"expected {planned.duration_seconds}, got {dur}"
                )

    def _verify_anti_loss_report(
        self,
        master_plan: MasterAVPlan,
        operational_scenes: List[Dict[str, Any]],
        director_metadata: Dict[str, Any],
        canonical_scenes: Optional[List[Scene]] = None,
        master_av_plan_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """Verifica efetivamente a integridade dos metadados preservados, operacionais e globais."""
        if canonical_scenes is None:
            canonical_scenes = master_plan.to_canonical_scenes()

        expected_ids = [s.scene_id for s in master_plan.scenes]
        expected_id_strs = {str(sid) for sid in expected_ids}
        op_ids = [s.get("scene_id") for s in operational_scenes]
        unconsumed = director_metadata.get("scenes_unconsumed", {})
        preserved_ids = [
            unconsumed[str(sid)].get("scene_id")
            for sid in expected_ids
            if str(sid) in unconsumed
        ]

        no_extra_unconsumed = set(unconsumed.keys()) == expected_id_strs
        scene_ids_match = (
            (expected_ids == op_ids)
            and (expected_ids == preserved_ids)
            and no_extra_unconsumed
        )

        all_planned_snapshots_match = all(
            str(s.scene_id) in unconsumed
            and unconsumed[str(s.scene_id)].get("planned_scene_full") == s.model_dump()
            for s in master_plan.scenes
        )

        all_vd_preserved = all(
            str(s.scene_id) in unconsumed
            and unconsumed[str(s.scene_id)].get("visual_direction_full") == s.visual_direction.model_dump()
            and unconsumed[str(s.scene_id)].get("extra_fields") == s.extra_fields
            for s in master_plan.scenes
        )

        operational_fields_coherent = (
            len(operational_scenes) == len(master_plan.scenes)
            and len(canonical_scenes) == len(master_plan.scenes)
        )
        if operational_fields_coherent:
            for s, canonical, op in zip(master_plan.scenes, canonical_scenes, operational_scenes):
                vd = s.visual_direction
                c_dump = canonical.model_dump()
                expected_visual_cues = (
                    s.extra_fields.get("visual_cues")
                    or s.continuity_notes.get("visual_cues")
                )
                expected_micro_cues = (
                    s.extra_fields.get("micro_cues")
                    or s.continuity_notes.get("micro_cues")
                )
                expected_op = {
                    "id": s.scene_id,
                    "scene_id": s.scene_id,
                    "order": s.order,
                    "narration": s.narration,
                    "text": s.narration,
                    "duration_s": s.duration_seconds,
                    "duration_seconds": s.duration_seconds,
                    "timestamp": s.timestamp,
                    "narrative_purpose": c_dump.get("narrative_purpose", s.narrative_purpose),
                    "emotion": c_dump.get("emotion"),
                    "scene_type": c_dump.get("scene_type"),
                    "b_roll_visual": c_dump.get("b_roll_visual") or vd.what_viewer_sees,
                    "b_roll_query": c_dump.get("b_roll_query"),
                    "visual_production_type": c_dump.get("visual_production_type", vd.visual_production_type),
                    "camera_framing": c_dump.get("camera_framing", vd.camera_framing),
                    "camera_movement": c_dump.get("camera_movement", vd.camera_movement),
                    "lighting_mood": c_dump.get("lighting_mood", vd.lighting_mood),
                    "transition_in": s.transition_in,
                    "transition_out": s.transition_out,
                    "continuity_anchor": c_dump.get("continuity_anchor"),
                    "sfx_cue": c_dump.get("sfx_cue"),
                    "on_screen_text": c_dump.get("on_screen_text"),
                    "estimated_cost_tier": c_dump.get("estimated_cost_tier"),
                    "visual_cues": expected_visual_cues,
                    "micro_cues": expected_micro_cues,
                }
                for field_name in MAPPED_TO_PIPELINE_B_FIELDS:
                    if op.get(field_name) != expected_op[field_name]:
                        operational_fields_coherent = False
                        break
                if not operational_fields_coherent:
                    break

        global_metadata_preserved = (
            director_metadata.get("plan_id") == master_plan.plan_id
            and director_metadata.get("schema_version") == master_plan.schema_version
            and director_metadata.get("production_profile") == master_plan.production_profile
            and director_metadata.get("central_theme") == master_plan.central_theme
            and director_metadata.get("visual_bible") == master_plan.visual_bible.model_dump()
            and director_metadata.get("execution_metadata") == master_plan.execution_metadata.model_dump()
            and director_metadata.get("validation_summary") == master_plan.validation_summary
        )

        summary_preserved = True
        if master_av_plan_summary is not None:
            summary_preserved = (
                master_av_plan_summary.get("plan_id") == master_plan.plan_id
                and master_av_plan_summary.get("schema_version") == master_plan.schema_version
                and master_av_plan_summary.get("created_at") == master_plan.created_at
                and master_av_plan_summary.get("production_profile") == master_plan.production_profile
                and master_av_plan_summary.get("total_scenes") == master_plan.total_scenes
                and master_av_plan_summary.get("total_duration_seconds") == master_plan.total_duration_seconds
            )

        all_ok = (
            scene_ids_match
            and all_planned_snapshots_match
            and all_vd_preserved
            and operational_fields_coherent
            and global_metadata_preserved
            and summary_preserved
        )

        checks = {
            "scene_ids_match": scene_ids_match,
            "all_planned_snapshots_match": all_planned_snapshots_match,
            "all_visual_directions_preserved": all_vd_preserved,
            "operational_fields_coherent": operational_fields_coherent,
            "global_metadata_preserved": global_metadata_preserved,
            "summary_preserved": summary_preserved,
            "no_unmapped_fields_dropped": all_ok,
        }

        if not all_ok:
            raise MasterPlanDataLossError(f"Anti-loss verification failed: {checks}")

        return checks
