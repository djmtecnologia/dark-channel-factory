"""
Testes unitários do MasterPlanAdapter — Sprint 2.5 (Tarefa 1).
Cobre conversão determinística, preservação anti-perda, imutabilidade
e compatibilidade com shared.scene_normalizer.normalize_scenes().
"""
import copy
import pytest

from agents.video_director.master_plan_models import (
    ExecutionMetadata,
    MasterAVPlan,
    StageExecutionMetrics,
)
from agents.video_director.models import (
    PlannedScene,
    SceneVisualDirection,
    VisualBible,
    VisualLanguage,
)
from agents.video_director.pipeline_adapter import (
    MasterPlanAdapter,
    MasterPlanAdapterError,
    MasterPlanDataLossError,
)
from shared.models import (
    CameraFraming,
    CameraMovement,
    Emotion,
    NarrativePurpose,
    SceneType,
    VisualProductionType,
)
from shared.scene_normalizer import normalize_scenes


def _build_sample_master_plan() -> MasterAVPlan:
    vb = VisualBible(
        visual_language=VisualLanguage.DOCUMENTARY_REALISTIC,
        dominant_style="Dark cinematic documentary",
        color_palette=["#0B0F19", "#D4AF37", "#E5E7EB"],
        lighting_mood="chiaroscuro_low_key",
        pacing_rhythm="measured_tension",
    )

    s1 = PlannedScene(
        scene_id=1,
        order=1,
        narration="  In 1999, a silent anomaly appeared on radar screens.  ",
        narrative_role="opening_hook",
        narrative_purpose=NarrativePurpose.HOOK,
        visual_intent="Establish mysterious radar anomaly",
        duration_seconds=8.0,
        start_time=0.0,
        end_time=8.0,
        timestamp="0:00-0:08",
        visual_direction=SceneVisualDirection(
            scene_id=1,
            narration="  In 1999, a silent anomaly appeared on radar screens.  ",
            what_viewer_sees="Green CRT radar screen pulsing in a dark control room",
            dominant_element="Radar sweep",
            visual_production_type=VisualProductionType.STOCK_FOOTAGE,
            camera_framing=CameraFraming.CLOSE_UP,
            camera_movement=CameraMovement.SLOW_PUSH_IN,
            depth_of_field="shallow_focus",
            composition="center_weighted",
            lighting_mood="dark_cinematic",
            atmosphere="tense_mystery",
            b_roll_visual="Dark military radar room with glowing green screen",
            b_roll_query="military radar screen dark",
            sfx_cue="low_radar_ping",
            emotion=Emotion.CURIOSITY,
            scene_type=SceneType.HOOK,
        ),
        transition_in="direct_cut",
        transition_out="crossfade_slow",
        transition_rationale="Smooth entry into context",
        continuity_notes={"visual_cues": ["radar_glow"], "micro_cues": ["ping_01"]},
        planning_rationale="Hook scene under 10s",
        extra_fields={"custom_tag": "radar_1999"},
    )

    s2 = PlannedScene(
        scene_id=2,
        order=2,
        narration="Engineers initially dismissed the signal as atmospheric noise.",
        narrative_role="context_setup",
        narrative_purpose=NarrativePurpose.CONTEXT,
        visual_intent="Show skeptical engineers reviewing logs",
        duration_seconds=12.0,
        start_time=8.0,
        end_time=20.0,
        timestamp="0:08-0:20",
        visual_direction=SceneVisualDirection(
            scene_id=2,
            narration="Engineers initially dismissed the signal as atmospheric noise.",
            what_viewer_sees="Engineers examining printed telemetry paper",
            dominant_element="Telemetry printout",
            visual_production_type=VisualProductionType.CINEMATIC_STILL,
            camera_framing=CameraFraming.MEDIUM,
            camera_movement=CameraMovement.TRACKING_LATERAL,
            b_roll_visual="Engineers reviewing paper telemetry in dim office",
            b_roll_query="engineers paper telemetry",
            sfx_cue="paper_rustle",
            emotion=Emotion.TRUST,
            scene_type=SceneType.PROBLEM,
        ),
        extra_fields={"micro_cues": [{"cue": "log_highlight"}]},
    )

    exec_meta = ExecutionMetadata(
        total_duration_seconds=1.25,
        production_profile="PILOT_5MIN",
        stages=[
            StageExecutionMetrics(
                stage="scene_planner",
                status="completed",
                duration_seconds=0.5,
                scene_count_in=2,
                scene_count_out=2,
            )
        ],
        components={"scene_planner": "ScenePlanner"},
        warnings=[],
    )

    return MasterAVPlan(
        plan_id="avplan_test_001",
        schema_version="2.4.0",
        production_profile="PILOT_5MIN",
        central_theme="The 1999 Radar Anomaly",
        visual_bible=vb,
        total_scenes=2,
        total_duration_seconds=20.0,
        scenes=[s1, s2],
        validation_summary={"passed": True},
        execution_metadata=exec_meta,
    )


def test_01_valid_master_plan_conversion():
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()
    result = adapter.to_pipeline_b(plan)

    assert len(result.scenes) == 2
    assert result.master_av_plan_summary["plan_id"] == "avplan_test_001"
    assert result.master_av_plan_summary["total_scenes"] == 2
    assert result.preserved_fields_report["total_scenes_converted"] == 2


def test_02_narration_preserved_verbatim():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    for planned, op in zip(plan.scenes, result.scenes):
        assert op["narration"] == planned.narration
        assert op["text"] == planned.narration


def test_03_identifiers_preserved():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    for planned, op in zip(plan.scenes, result.scenes):
        assert op["id"] == planned.scene_id
        assert op["scene_id"] == planned.scene_id


def test_04_order_preserved():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    orders = [s["order"] for s in result.scenes]
    assert orders == [1, 2]


def test_05_durations_preserved():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    for planned, op in zip(plan.scenes, result.scenes):
        assert op["duration_s"] == planned.duration_seconds
        assert op["duration_seconds"] == planned.duration_seconds


def test_06_visual_metadata_mapped():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    s1 = result.scenes[0]
    assert s1["b_roll_visual"] == "Dark military radar room with glowing green screen"
    assert s1["b_roll_query"] == "military radar screen dark"
    assert s1["sfx_cue"] == "low_radar_ping"
    assert s1["camera_framing"] == "close_up"
    assert s1["camera_movement"] == "slow_push_in"
    assert s1["narrative_purpose"] == "hook"
    assert s1["visual_production_type"] == "stock_footage"
    assert s1["visual_cues"] == ["radar_glow"]
    assert s1["micro_cues"] == ["ping_01"]
    assert result.scenes[1]["micro_cues"] == [{"cue": "log_highlight"}]


def test_07_unconsumed_fields_preserved_separately():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    meta = result.director_metadata
    assert meta["visual_bible"]["dominant_style"] == "Dark cinematic documentary"
    assert meta["visual_bible"]["color_palette"] == ["#0B0F19", "#D4AF37", "#E5E7EB"]
    assert meta["execution_metadata"]["production_profile"] == "PILOT_5MIN"
    assert meta["validation_summary"] == {"passed": True}

    unconsumed_s1 = meta["scenes_unconsumed"]["1"]
    assert unconsumed_s1["scene_id"] == 1
    assert unconsumed_s1["narrative_role"] == "opening_hook"
    assert unconsumed_s1["visual_intent"] == "Establish mysterious radar anomaly"
    assert unconsumed_s1["transition_rationale"] == "Smooth entry into context"
    assert unconsumed_s1["planning_rationale"] == "Hook scene under 10s"
    assert unconsumed_s1["visual_direction_full"] == plan.scenes[0].visual_direction.model_dump()
    assert unconsumed_s1["extra_fields"]["custom_tag"] == "radar_1999"


def test_08_compatibility_with_normalize_scenes_and_strip_behavior():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    # Adapter preserva byte a byte (incluindo espaços nas extremidades)
    assert result.scenes[0]["narration"] == plan.scenes[0].narration
    assert result.scenes[0]["narration"].startswith("  ")

    # normalize_scenes() aplica .strip() nas extremidades, preservando o conteúdo textual
    normalized = normalize_scenes(result.scenes, fallback_topic="Radar Anomaly")
    assert len(normalized) == 2
    assert normalized[0]["id"] == 1
    assert normalized[0]["narration"] == plan.scenes[0].narration.strip()
    assert normalized[0]["duration_s"] == 8.0
    assert normalized[0]["b_roll_query"] == "military radar screen dark"


def test_09_detects_scene_count_mismatch():
    plan = _build_sample_master_plan()
    # Simula divergência interna sem passar pelo validador Pydantic
    object.__setattr__(plan, "total_scenes", 99)

    with pytest.raises(MasterPlanDataLossError, match="Scene count mismatch"):
        MasterPlanAdapter().to_pipeline_b(plan)


def test_10_detects_information_loss_in_validation():
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()
    corrupted_scenes = [
        {"id": 1, "scene_id": 1, "order": 1, "narration": "TRUNCATED", "duration_s": 8.0},
        {"id": 2, "scene_id": 2, "order": 2, "narration": plan.scenes[1].narration, "duration_s": 12.0},
    ]

    with pytest.raises(MasterPlanDataLossError, match="Narration integrity violation"):
        adapter._validate_output_integrity(plan, corrupted_scenes)


def test_11_no_mutation_of_source_objects():
    plan = _build_sample_master_plan()
    snapshot_before = copy.deepcopy(plan.model_dump())

    result = MasterPlanAdapter().to_pipeline_b(plan)
    # Mutar o resultado não deve afetar o plano original
    result.scenes[0]["narration"] = "MUTATED"
    result.director_metadata["visual_bible"]["color_palette"].append("#FFFFFF")

    assert plan.model_dump() == snapshot_before


def test_12_deterministic_conversion():
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()

    r1 = adapter.to_pipeline_b(plan)
    r2 = adapter.to_pipeline_b(plan)

    assert r1.scenes == r2.scenes
    assert r1.director_metadata == r2.director_metadata
    assert r1.master_av_plan_summary == r2.master_av_plan_summary


def test_invalid_input_type_raises_adapter_error():
    with pytest.raises(MasterPlanAdapterError, match="Expected MasterAVPlan"):
        MasterPlanAdapter().to_pipeline_b({"not": "a_plan"})  # type: ignore[arg-type]


def test_13_anti_loss_report_integrity_and_categories():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)
    report = result.preserved_fields_report

    assert "mapped_to_pipeline_b" in report
    assert "effectively_consumed_by_pipeline_b" in report
    assert "preserved_without_consumer" in report
    assert "transformed_by_normalizer" in report
    assert "narration" in report["transformed_by_normalizer"]

    checks = report["integrity_checks"]
    assert checks["scene_ids_match"] is True
    assert checks["all_planned_snapshots_match"] is True
    assert checks["all_visual_directions_preserved"] is True
    assert checks["operational_fields_coherent"] is True
    assert checks["global_metadata_preserved"] is True
    assert checks["no_unmapped_fields_dropped"] is True


def test_14_planned_scene_full_snapshot_matches_origin():
    plan = _build_sample_master_plan()
    result = MasterPlanAdapter().to_pipeline_b(plan)

    for planned in plan.scenes:
        preserved = result.director_metadata["scenes_unconsumed"][str(planned.scene_id)]
        assert preserved["planned_scene_full"] == planned.model_dump()


@pytest.mark.parametrize(
    "corrupt_field,corrupt_value",
    [
        ("narrative_role", "CORRUPTED_ROLE"),
        ("continuity_notes", {"corrupted": True}),
        ("planning_rationale", "CORRUPTED_RATIONALE"),
        ("transition_rationale", "CORRUPTED_TRANSITION"),
    ],
)
def test_15_rejects_corrupted_planned_scene_snapshot_fields(corrupt_field, corrupt_value):
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()
    result = adapter.to_pipeline_b(plan)

    corrupted_meta = copy.deepcopy(result.director_metadata)
    corrupted_meta["scenes_unconsumed"]["1"]["planned_scene_full"][corrupt_field] = corrupt_value

    with pytest.raises(MasterPlanDataLossError, match="Anti-loss verification failed"):
        adapter._verify_anti_loss_report(plan, result.scenes, corrupted_meta)


@pytest.mark.parametrize(
    "op_field,corrupt_value",
    [
        ("id", 999),
        ("scene_id", 999),
        ("order", 99),
        ("narration", "CORRUPTED_NARRATION"),
        ("text", "CORRUPTED_TEXT"),
        ("duration_s", 999.0),
        ("duration_seconds", 999.0),
        ("timestamp", "9:99-9:99"),
        ("narrative_purpose", "cta"),
        ("emotion", "fear"),
        ("scene_type", "outro"),
        ("b_roll_visual", "CORRUPTED_VISUAL"),
        ("b_roll_query", "corrupted query"),
        ("visual_production_type", "ai_video"),
        ("camera_framing", "extreme_wide"),
        ("camera_movement", "orbit"),
        ("lighting_mood", "bright_daylight"),
        ("transition_in", "corrupted_in"),
        ("transition_out", "corrupted_out"),
        ("continuity_anchor", "corrupted_anchor"),
        ("sfx_cue", "CORRUPTED_SFX"),
        ("on_screen_text", "CORRUPTED_TEXT"),
        ("estimated_cost_tier", "high"),
        ("visual_cues", ["corrupted_cue"]),
        ("micro_cues", ["corrupted_micro"]),
    ],
)
def test_16_rejects_corrupted_operational_fields(op_field, corrupt_value):
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()
    result = adapter.to_pipeline_b(plan)

    corrupted_scenes = copy.deepcopy(result.scenes)
    corrupted_scenes[0][op_field] = corrupt_value

    with pytest.raises(MasterPlanDataLossError, match="Anti-loss verification failed"):
        adapter._verify_anti_loss_report(
            plan,
            corrupted_scenes,
            result.director_metadata,
            master_av_plan_summary=result.master_av_plan_summary,
        )


@pytest.mark.parametrize(
    "summary_field,corrupt_value",
    [
        ("plan_id", "corrupted_plan_id"),
        ("schema_version", "9.9.9"),
        ("created_at", "1999-01-01T00:00:00Z"),
        ("production_profile", "SHORTS"),
        ("total_scenes", 999),
        ("total_duration_seconds", 999.0),
    ],
)
def test_17_rejects_corrupted_master_av_plan_summary(summary_field, corrupt_value):
    plan = _build_sample_master_plan()
    adapter = MasterPlanAdapter()
    result = adapter.to_pipeline_b(plan)

    corrupted_summary = copy.deepcopy(result.master_av_plan_summary)
    corrupted_summary[summary_field] = corrupt_value

    with pytest.raises(MasterPlanDataLossError, match="Anti-loss verification failed"):
        adapter._verify_anti_loss_report(
            plan,
            result.scenes,
            result.director_metadata,
            master_av_plan_summary=corrupted_summary,
        )

