"""
Testes obrigatórios para o ScenePlanner (Sprint 2.3).
100% determinísticos, offline e sem chamadas pagas de LLM.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Dict, List

import pytest

from agents.video_director.models import (
    NarrativeAnalysisResult,
    PlannedScene,
    SceneNarrativeInterpretation,
    ScenePlanResult,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionResult,
    VisualLanguage,
)
from agents.video_director.scene_planner import (
    DurationMismatchError,
    IncompleteLLMResponseError,
    ScenePlanner,
    ScenePlannerValidationError,
)
from shared.models import CameraFraming, CameraMovement, NarrativePurpose, Scene
from shared.production_profiles import ProductionProfileType
from shared.scene_normalizer import normalize_scene_v2


def _make_visual_bible() -> VisualBible:
    return VisualBible(
        visual_language=VisualLanguage.DOCUMENTARY_REALISTIC,
        dominant_style="dark_investigative_cinema",
        color_palette=["#0B0F19", "#1E293B", "#D97706"],
        lighting_mood="dark_cinematic",
        pacing_rhythm="measured_suspense",
    )


def _make_scene_pair(
    scene_id: int,
    narration: str,
    purpose: NarrativePurpose = NarrativePurpose.CONTEXT,
    duration_s: float | None = None,
    framing: CameraFraming = CameraFraming.WIDE,
    lighting: str = "dark_cinematic",
    transition_in: str = "direct_cut",
    transition_out: str = "crossfade_slow",
    dramatic_importance: float = 0.5,
    b_roll_query: str = "dark archive room",
) -> tuple[SceneNarrativeInterpretation, SceneVisualDirection]:
    interp = SceneNarrativeInterpretation(
        scene_id=scene_id,
        original_narration=narration,
        scene_intent=f"Intent for scene {scene_id}",
        narrative_purpose=purpose,
        dramatic_importance=dramatic_importance,
        duration_s=duration_s,
    )
    direction = SceneVisualDirection(
        scene_id=scene_id,
        narration=narration,
        what_viewer_sees=f"Visual for scene {scene_id}",
        dominant_element="archival_document",
        camera_framing=framing,
        camera_movement=CameraMovement.SLOW_PUSH_IN,
        lighting_mood=lighting,
        transition_in=transition_in,
        transition_out=transition_out,
        b_roll_visual=f"Detailed cinematic shot for scene {scene_id}",
        b_roll_query=b_roll_query,
        duration_s=duration_s,
    )
    return interp, direction


def _build_inputs(
    count: int = 3,
    duration_s: float | None = 100.0,
    start_id: int = 1,
) -> tuple[NarrativeAnalysisResult, VisualDirectionResult]:
    interps: List[SceneNarrativeInterpretation] = []
    dirs: List[SceneVisualDirection] = []
    purposes = [NarrativePurpose.HOOK, NarrativePurpose.CONTEXT, NarrativePurpose.REVELATION]
    framings = [CameraFraming.WIDE, CameraFraming.MEDIUM, CameraFraming.CLOSE_UP]

    for i in range(count):
        sid = start_id + i
        interp, direction = _make_scene_pair(
            scene_id=sid,
            narration=f"Narration text for scene {sid}.",
            purpose=purposes[i % len(purposes)],
            duration_s=duration_s,
            framing=framings[i % len(framings)],
            dramatic_importance=0.3 + 0.2 * (i % 3),
        )
        interps.append(interp)
        dirs.append(direction)

    na = NarrativeAnalysisResult(
        central_theme="Mystery of the Lost Signal",
        historical_or_factual_context="1977 Deep Space",
        narrative_objective="Investigate the signal",
        narrative_progression=["Hook", "Investigation", "Revelation"],
        scene_analyses=interps,
        total_scenes=count,
    )
    vd = VisualDirectionResult(
        visual_bible=_make_visual_bible(),
        scene_directions=dirs,
    )
    return na, vd


# ─────────────────────────────────────────────────────────────
# Cenários de entrada
# ─────────────────────────────────────────────────────────────

def test_few_scenes_script():
    na, vd = _build_inputs(count=3, duration_s=100.0)
    planner = ScenePlanner()
    result = planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert isinstance(result, ScenePlanResult)
    assert result.scene_count == 3
    assert result.total_duration_seconds == 300.0


def test_extensive_script_30_scenes():
    na, vd = _build_inputs(count=30, duration_s=20.0)
    planner = ScenePlanner()
    result = planner.plan_scenes(na, vd, ProductionProfileType.STANDARD_10MIN)
    assert result.scene_count == 30
    assert result.total_duration_seconds == 600.0


def test_single_scene_script():
    na, vd = _build_inputs(count=1, duration_s=300.0)
    planner = ScenePlanner()
    result = planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert result.scene_count == 1
    assert result.scenes[0].order == 1


def test_empty_scenes_rejected():
    na, vd = _build_inputs(count=1, duration_s=300.0)
    na.scene_analyses = []
    na.total_scenes = 0
    planner = ScenePlanner()
    with pytest.raises(ScenePlannerValidationError):
        planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_incomplete_original_scenes_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    planner = ScenePlanner()
    with pytest.raises(ScenePlannerValidationError):
        planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=[{"id": 1}])


# ─────────────────────────────────────────────────────────────
# Duração e Perfis
# ─────────────────────────────────────────────────────────────

def test_explicit_durations_preserved():
    na, vd = _build_inputs(count=3, duration_s=None)
    orig = [
        {"id": 1, "narration": "Narration text for scene 1.", "duration_s": 80.0},
        {"id": 2, "narration": "Narration text for scene 2.", "duration_s": 120.0},
        {"id": 3, "narration": "Narration text for scene 3.", "duration_s": 100.0},
    ]
    planner = ScenePlanner()
    result = planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=orig)
    assert [s.duration_seconds for s in result.scenes] == [80.0, 120.0, 100.0]


def test_unplanned_durations_distributed_by_weight_not_equally():
    i1, d1 = _make_scene_pair(1, "Short.", dramatic_importance=0.1)
    i2, d2 = _make_scene_pair(2, "Very long and dense documentary narration with many words.", dramatic_importance=1.0)
    na = NarrativeAnalysisResult(
        central_theme="Theme",
        historical_or_factual_context="Context",
        narrative_objective="Objective",
        narrative_progression=["A", "B", "C"],
        scene_analyses=[i1, i2],
        total_scenes=2,
    )
    vd = VisualDirectionResult(visual_bible=_make_visual_bible(), scene_directions=[d1, d2])
    result = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert result.scenes[1].duration_seconds > result.scenes[0].duration_seconds


def test_compatible_and_incompatible_total_duration():
    na, vd = _build_inputs(count=3, duration_s=100.0)  # 300s -> PILOT_5MIN [270, 330]
    planner = ScenePlanner()
    assert planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN).total_duration_seconds == 300.0

    na_bad, vd_bad = _build_inputs(count=3, duration_s=50.0)  # 150s -> incompatível
    with pytest.raises(DurationMismatchError):
        planner.plan_scenes(na_bad, vd_bad, ProductionProfileType.PILOT_5MIN)


@pytest.mark.parametrize("bad_dur", [0.0, -5.0, float("nan"), float("inf")])
def test_invalid_durations_rejected(bad_dur: float):
    na, vd = _build_inputs(count=2, duration_s=150.0)
    orig = [
        {"id": 1, "narration": "Narration text for scene 1.", "duration_s": bad_dur},
        {"id": 2, "narration": "Narration text for scene 2.", "duration_s": 150.0},
    ]
    with pytest.raises(DurationMismatchError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=orig)


def test_profiles_pilot_standard_documentary():
    planner = ScenePlanner()
    na5, vd5 = _build_inputs(count=3, duration_s=100.0)
    assert planner.plan_scenes(na5, vd5, ProductionProfileType.PILOT_5MIN).profile == "PILOT_5MIN"

    na10, vd10 = _build_inputs(count=6, duration_s=100.0)
    assert planner.plan_scenes(na10, vd10, ProductionProfileType.STANDARD_10MIN).profile == "STANDARD_10MIN"

    na20, vd20 = _build_inputs(count=12, duration_s=100.0)
    assert planner.plan_scenes(na20, vd20, ProductionProfileType.DOCUMENTARY_20MIN).profile == "DOCUMENTARY_20MIN"


# ─────────────────────────────────────────────────────────────
# Integridade
# ─────────────────────────────────────────────────────────────

def test_duplicate_ids_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    na.scene_analyses[1].scene_id = 1
    with pytest.raises(ScenePlannerValidationError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_out_of_order_scenes_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    vd.scene_directions = [vd.scene_directions[1], vd.scene_directions[0]]
    with pytest.raises(ScenePlannerValidationError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_missing_scene_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    vd.scene_directions = [vd.scene_directions[0]]
    with pytest.raises(ScenePlannerValidationError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_llm_invented_scene_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    def fake_llm(_prompt: str, timeout: float = 10.0) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}, {"scene_id": 2}, {"scene_id": 999}]})
    planner = ScenePlanner(llm_client=fake_llm)
    with pytest.raises(ScenePlannerValidationError):
        planner.plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_altered_narration_rejected():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    vd.scene_directions[0].narration = "Modified narration"
    with pytest.raises(ScenePlannerValidationError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)


def test_preserve_unicode_spaces_newlines_id_zero_and_timestamp_zero():
    exact_text = "  Atenção: 1977 — sinal\n\ncom espaços   e acentuação!  "
    na, vd = _build_inputs(count=2, duration_s=150.0, start_id=0)
    na.scene_analyses[0].original_narration = exact_text
    vd.scene_directions[0].narration = exact_text
    orig = [
        {"id": 0, "narration": exact_text, "duration_s": 150.0, "timestamp": 0},
        {"id": 1, "narration": "Narration text for scene 1.", "duration_s": 150.0},
    ]
    result = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=orig)
    assert result.scenes[0].scene_id == 0
    assert result.scenes[0].narration == exact_text
    assert result.scenes[0].extra_fields["original_timestamp"] == 0


# ─────────────────────────────────────────────────────────────
# Continuidade
# ─────────────────────────────────────────────────────────────

def test_visual_continuity_and_framing_repetition_detection():
    na, vd = _build_inputs(count=3, duration_s=100.0)
    for d in vd.scene_directions:
        d.camera_framing = CameraFraming.WIDE
    result = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert result.scenes[2].continuity_notes["consecutive_framing_count"] == 3
    assert result.scenes[2].continuity_notes["framing_repetition_warning"] is True


def test_justified_environment_change_and_transitions():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    vd.scene_directions[1].lighting_mood = "cold_fluorescent"
    vd.scene_directions[1].transition_in = "fade_to_black"
    result = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert result.scenes[1].continuity_notes["lighting_shift_justified"] is True
    assert "fade_to_black" in result.scenes[1].transition_rationale
    assert result.scenes[0].continuity_notes["visual_bible_language"] == "documentary_realistic"


# ─────────────────────────────────────────────────────────────
# Integração, Contrato V2, Serialização e LLM
# ─────────────────────────────────────────────────────────────

def test_v2_contract_and_serialization():
    na, vd = _build_inputs(count=3, duration_s=100.0)
    result = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    canonical = result.to_canonical_scenes()
    assert len(canonical) == 3
    assert all(isinstance(c, Scene) for c in canonical)
    norm = normalize_scene_v2(canonical[0])
    assert norm["id"] == 1
    assert norm["duration_s"] == 100.0

    # Serialização e desserialização
    json_str = result.model_dump_json()
    restored = ScenePlanResult.model_validate_json(json_str)
    assert restored.total_duration_seconds == result.total_duration_seconds
    assert restored.scene_count == 3


def test_llm_incomplete_response_timeout_and_fallback():
    na, vd = _build_inputs(count=2, duration_s=150.0)

    # Resposta incompleta
    def incomplete_llm(_p: str) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}]})

    with pytest.raises(IncompleteLLMResponseError):
        ScenePlanner(llm_client=incomplete_llm).plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)

    # Timeout assíncrono
    async def slow_llm(_p: str) -> str:
        await asyncio.sleep(0.2)
        return "{}"

    with pytest.raises(IncompleteLLMResponseError):
        ScenePlanner(llm_client=slow_llm, timeout=0.01).plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)

    # Falha de provedor com sanitização de credencial e fallback
    def failing_llm(_p: str, timeout: float = 10.0) -> str:
        raise RuntimeError("Auth failed with Bearer sk-1234567890abcdef1234567890")

    with pytest.raises(IncompleteLLMResponseError) as exc_info:
        ScenePlanner(llm_client=failing_llm, fallback_on_llm_error=False).plan_scenes(
            na, vd, ProductionProfileType.PILOT_5MIN
        )
    assert "sk-1234567890" not in str(exc_info.value)

    # Fallback ativado
    res_fb = ScenePlanner(llm_client=failing_llm, fallback_on_llm_error=True).plan_scenes(
        na, vd, ProductionProfileType.PILOT_5MIN
    )
    assert res_fb.scene_count == 2



# ============================================================================
# AUDITORIA INDEPENDENTE SPRINT 2.3 — NOVOS TESTES
# ============================================================================


def test_llm_duplicate_missing_extra_and_valid_ids():
    na, vd = _build_inputs(count=2, duration_s=150.0)

    # 1. IDs corretos e únicos
    def valid_llm(_p: str, timeout: float = 10.0) -> str:
        return json.dumps({
            "scenes": [
                {"scene_id": 1, "narrative_role": "hook"},
                {"scene_id": 2, "narrative_role": "climax"},
            ]
        })
    res = ScenePlanner(llm_client=valid_llm).plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert res.scene_count == 2

    # 2. IDs duplicados (2 cenas, ambas com scene_id=1)
    def dup_llm(_p: str, timeout: float = 10.0) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}, {"scene_id": 1}]})
    with pytest.raises(ScenePlannerValidationError, match="duplicate"):
        ScenePlanner(llm_client=dup_llm).plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)

    # 3. Todos os IDs esperados + um ID duplicado adicional (cena extra)
    def extra_dup_llm(_p: str, timeout: float = 10.0) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}, {"scene_id": 2}, {"scene_id": 1}]})
    with pytest.raises(ScenePlannerValidationError, match="duplicate"):
        ScenePlanner(llm_client=extra_dup_llm).plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)

    # 4. Cena ausente
    def missing_llm(_p: str, timeout: float = 10.0) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}]})
    with pytest.raises(IncompleteLLMResponseError, match="missing scenes"):
        ScenePlanner(llm_client=missing_llm, fallback_on_llm_error=False).plan_scenes(
            na, vd, ProductionProfileType.PILOT_5MIN
        )


@pytest.mark.parametrize("bad_dur", [0.1, 0.99, "abc", "", True])
def test_duration_v2_boundary_rejected(bad_dur):
    na, vd = _build_inputs(count=2, duration_s=150.0)
    orig = [
        {"id": 1, "narration": na.scene_analyses[0].original_narration, "duration_s": bad_dur},
        {"id": 2, "narration": na.scene_analyses[1].original_narration, "duration_s": 299.0},
    ]
    with pytest.raises(DurationMismatchError):
        ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=orig)


@pytest.mark.parametrize("good_dur", [1.0, 1.01])
def test_duration_v2_boundary_accepted(good_dur):
    na, vd = _build_inputs(count=2, duration_s=150.0)
    orig = [
        {"id": 1, "narration": na.scene_analyses[0].original_narration, "duration_s": good_dur},
        {"id": 2, "narration": na.scene_analyses[1].original_narration, "duration_s": round(300.0 - good_dur, 3)},
    ]
    res = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN, original_scenes=orig)
    assert res.scenes[0].duration_seconds == pytest.approx(good_dur)
    canonical = res.to_canonical_scenes()
    assert canonical[0].duration_s >= 1.0


def test_scene_plan_result_direct_integrity_validation():
    from agents.video_director.models import PlannedScene, ScenePlanResult
    na, vd = _build_inputs(count=2, duration_s=150.0)
    valid_res = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    s1, s2 = valid_res.scenes[0], valid_res.scenes[1]

    def _make_result(scenes_list, total_dur=300.0):
        return ScenePlanResult(
            profile="pilot_5min",
            total_duration_seconds=total_dur,
            scene_count=len(scenes_list),
            scenes=scenes_list,
            visual_bible=valid_res.visual_bible,
        )

    # 1. IDs duplicados
    s2_dup = s2.model_copy(update={"scene_id": 1})
    with pytest.raises(ValueError, match="Duplicate scene_id"):
        _make_result([s1, s2_dup])

    # 2. Order incoerente com posição
    s2_bad_order = s2.model_copy(update={"order": 5})
    with pytest.raises(ValueError, match="order="):
        _make_result([s1, s2_bad_order])

    # 3. Primeira cena não iniciando em zero
    s1_nonzero = s1.model_copy(update={"start_time": 1.0, "end_time": 151.0})
    with pytest.raises(ValueError, match="start at 0.0"):
        _make_result([s1_nonzero, s2])

    # 4. Gap entre cenas consecutivas
    s2_gap = s2.model_copy(update={"start_time": 155.0, "end_time": 305.0})
    with pytest.raises(ValueError, match="gap or overlap"):
        _make_result([s1, s2_gap])

    # 5. duration_seconds != end_time - start_time
    s1_bad_span = s1.model_copy(update={"end_time": 100.0})
    with pytest.raises(ValueError, match="does not match"):
        _make_result([s1_bad_span, s2])

    # 6. total_duration_seconds != soma das durações
    with pytest.raises(ValueError, match="total_duration_seconds"):
        _make_result([s1, s2], total_dur=999.0)


def test_invoke_llm_timeout_enforcement():
    na, vd = _build_inputs(count=2, duration_s=150.0)

    # Cliente síncrono SEM parâmetro timeout deve ser rejeitado
    def sync_no_timeout(_p: str) -> str:
        return json.dumps({"scenes": [{"scene_id": 1}, {"scene_id": 2}]})
    with pytest.raises(IncompleteLLMResponseError, match="timeout"):
        ScenePlanner(llm_client=sync_no_timeout, fallback_on_llm_error=False).plan_scenes(
            na, vd, ProductionProfileType.PILOT_5MIN
        )

    # Cliente síncrono COM timeout que expira preserva causa
    def sync_timeout_raises(_p: str, timeout: float = 1.0) -> str:
        raise TimeoutError("sync socket timed out")
    with pytest.raises(IncompleteLLMResponseError) as exc_info:
        ScenePlanner(llm_client=sync_timeout_raises, fallback_on_llm_error=False).plan_scenes(
            na, vd, ProductionProfileType.PILOT_5MIN
        )
    assert isinstance(exc_info.value.__cause__, TimeoutError)


def test_visual_continuity_intentional_vs_accidental():
    na, vd = _build_inputs(count=3, duration_s=100.0)
    # 3 cenas com mesmo framing sem continuity_anchor -> redundância acidental
    for d in vd.scene_directions:
        d.camera_framing = "medium_shot"
        d.continuity_anchor = None
        d.visual_connection_previous = None
    res = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert res.scenes[2].continuity_notes["framing_repetition_warning"] is True
    assert res.scenes[2].continuity_notes["variation_recommendation"] is not None
    assert len(res.validation_summary["continuity_warnings"]) >= 1

    # Com continuity_anchor -> repetição intencional
    vd.scene_directions[2].continuity_anchor = "same_subject_close"
    res_int = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert res_int.scenes[2].continuity_notes["intentional_repetition"] is True
    assert res_int.scenes[2].continuity_notes["framing_repetition_warning"] is False


def test_unequal_weights_duration_distribution_near_minimum():
    na, vd = _build_inputs(count=3)
    for a, d in zip(na.scene_analyses, vd.scene_directions):
        a.duration_s = None
        d.duration_s = None
    na.scene_analyses[0].dramatic_importance = 0.0
    na.scene_analyses[0].emotional_intensity = 0.0
    na.scene_analyses[0].original_narration = "Um."
    vd.scene_directions[0].narration = "Um."
    na.scene_analyses[2].dramatic_importance = 1.0
    na.scene_analyses[2].emotional_intensity = 1.0
    long_text = " ".join(["palavra"] * 60)
    na.scene_analyses[2].original_narration = long_text
    vd.scene_directions[2].narration = long_text

    # Orçamento restante próximo do mínimo (3.05s para 3 cenas -> mínimo 1.0s cada)
    res = ScenePlanner().plan_scenes(
        na,
        vd,
        ProductionProfileType.PILOT_5MIN,
        planning_params={"target_duration_s": 3.05, "enforce_profile_duration": False},
    )
    assert all(s.duration_seconds >= 1.0 for s in res.scenes)
    assert res.total_duration_seconds == pytest.approx(3.05, abs=1e-3)


def test_profile_min_max_scenes_warning():
    na, vd = _build_inputs(count=2, duration_s=150.0)
    # PILOT_5MIN tem min_scenes >= 3 normalmente; 2 cenas deve registrar advertência sem bloquear
    res = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert "scene_count_within_profile_range" in res.validation_summary
    if not res.validation_summary["scene_count_within_profile_range"]:
        assert res.validation_summary["profile_scene_count_warning"] is not None


def test_validation_summary_profile_bounds_and_tolerance():
    na, vd = _build_inputs(count=2, duration_s=150.0)

    # 1. Duração dentro dos limites do PILOT_5MIN
    res_ok = ScenePlanner().plan_scenes(na, vd, ProductionProfileType.PILOT_5MIN)
    assert res_ok.validation_summary["durations_valid_and_finite"] is True
    assert res_ok.validation_summary["profile_bounds_respected"] is True

    # 2. Duração fora dos limites com enforcement habilitado -> DurationMismatchError
    na_out, vd_out = _build_inputs(count=2, duration_s=10.0)
    with pytest.raises(DurationMismatchError):
        ScenePlanner().plan_scenes(
            na_out,
            vd_out,
            ProductionProfileType.PILOT_5MIN,
            planning_params={"enforce_profile_duration": True},
        )

    # 3. Duração fora dos limites com enforcement desabilitado -> profile_bounds_respected=False
    res_unbounded = ScenePlanner().plan_scenes(
        na_out,
        vd_out,
        ProductionProfileType.PILOT_5MIN,
        planning_params={"enforce_profile_duration": False},
    )
    assert res_unbounded.validation_summary["durations_valid_and_finite"] is True
    assert res_unbounded.validation_summary["profile_bounds_respected"] is False
    assert res_unbounded.validation_summary["enforce_profile_duration"] is False

    # 4. Tolerância de duração configurada
    prof = ScenePlanner()._resolve_profile(ProductionProfileType.PILOT_5MIN)
    slightly_under = prof.min_duration_s - 5.0
    na_tol, vd_tol = _build_inputs(count=2, duration_s=slightly_under / 2.0)
    res_tol = ScenePlanner().plan_scenes(
        na_tol,
        vd_tol,
        ProductionProfileType.PILOT_5MIN,
        planning_params={"enforce_profile_duration": True, "duration_tolerance_s": 10.0},
    )
    assert res_tol.validation_summary["profile_bounds_respected"] is True
    assert res_tol.validation_summary["duration_tolerance_s"] == 10.0

    # 5. Valores inválidos e não finitos continuam rejeitados antes do resultado
    for bad in (float("nan"), float("inf"), -1.0, 0.5):
        na_bad, vd_bad = _build_inputs(count=2, duration_s=150.0)
        orig_bad = [
            {"id": 1, "narration": na_bad.scene_analyses[0].original_narration, "duration_s": bad},
            {"id": 2, "narration": na_bad.scene_analyses[1].original_narration, "duration_s": 150.0},
        ]
        with pytest.raises(DurationMismatchError):
            ScenePlanner().plan_scenes(na_bad, vd_bad, ProductionProfileType.PILOT_5MIN, original_scenes=orig_bad)
