"""
Testes automatizados do AI Video Director Engine (Sprint 2.4).
Cobre fluxo de sucesso, integridade, falhas por etapa, timeout,
imutabilidade, injeção de dependências e integração real dos 3 módulos.
"""
from __future__ import annotations

import copy
import json
import math
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agents.video_director import (
    AIVideoDirector,
    AIVideoDirectorError,
    MasterAVPlan,
    NarrativeAnalyzer,
    NarrativeAnalysisResult,
    PlannedScene,
    SceneNarrativeInterpretation,
    ScenePlanner,
    ScenePlanResult,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionResult,
    VisualDirector,
)
from shared.models import Scene


def _make_script(num_scenes: int = 3) -> Dict[str, Any]:
    return {
        "title": "The Hidden Architecture",
        "scenes": [
            {
                "id": idx,
                "narration": f"Cena {idx}: uma revelação documental sobre sistemas ocultos.",
            }
            for idx in range(1, num_scenes + 1)
        ],
    }


def _make_visual_bible() -> VisualBible:
    return VisualBible(
        visual_language="documentary_realistic",
        dominant_style="observational_documentary",
        color_palette=["#0F172A", "#38BDF8", "#F8FAFC"],
        lighting_mood="low_key_dramatic",
        pacing_rhythm="measured_investigative",
    )


def _make_narrative_result(script: Dict[str, Any]) -> NarrativeAnalysisResult:
    scenes = script["scenes"]
    return NarrativeAnalysisResult(
        central_theme="Sistemas ocultos",
        historical_or_factual_context="Contexto documental contemporâneo",
        narrative_objective="Expor a arquitetura oculta de forma clara",
        narrative_progression=["setup", "escalation", "revelation"],
        scene_analyses=[
            SceneNarrativeInterpretation(
                scene_id=s["id"],
                original_narration=s["narration"],
                scene_intent="Reveal hidden mechanism",
                narrative_tension=0.6,
                visual_impact_potential="high",
                evidence_needed=["document"],
            )
            for s in scenes
        ],
    )


def _make_visual_result(script: Dict[str, Any]) -> VisualDirectionResult:
    scenes = script["scenes"]
    framings = ["wide", "medium", "close_up"]
    movements = ["static", "slow_push_in", "tracking_lateral"]
    return VisualDirectionResult(
        visual_bible=_make_visual_bible(),
        scene_directions=[
            SceneVisualDirection(
                scene_id=s["id"],
                narration=s["narration"],
                b_roll_visual=f"Dark archival server room scene {s['id']}",
                b_roll_query=f"server room dark {s['id']}",
                what_viewer_sees=f"Dark archival server room scene {s['id']}",
                dominant_element="server rack",
                visual_production_type="stock_footage",
                camera_framing=framings[(s["id"] - 1) % len(framings)],
                camera_movement=movements[(s["id"] - 1) % len(movements)],
                lighting_mood="dramatic",
                emotion="curiosity",
                scene_type="hook" if s["id"] == 1 else "proof",
                continuity_anchor="slate_blue_tone",
            )
            for s in scenes
        ],
    )


def _make_plan_result(
    narrative: NarrativeAnalysisResult,
    visual: VisualDirectionResult,
    profile: str = "PILOT_5MIN",
) -> ScenePlanResult:
    planned: List[PlannedScene] = []
    t = 0.0
    dur = 10.0
    for idx, (na, vd) in enumerate(zip(narrative.scene_analyses, visual.scene_directions), start=1):
        planned.append(
            PlannedScene(
                scene_id=na.scene_id,
                order=idx,
                narration=na.original_narration,
                duration_seconds=dur,
                start_time=t,
                end_time=t + dur,
                timestamp=f"{t:.1f}s - {t + dur:.1f}s",
                narrative_purpose="hook" if idx == 1 else "tension",
                narrative_role="hook" if idx == 1 else "escalation",
                visual_intent="Establish dark archival mood",
                transition_in="fade_black" if idx == 1 else "cut",
                transition_out="cut",
                visual_direction=vd,
            )
        )
        t += dur
    return ScenePlanResult(
        profile=profile,
        total_duration_seconds=t,
        scene_count=len(planned),
        scenes=planned,
        visual_bible=visual.visual_bible,
        validation_summary={"status": "passed", "warnings": []},
    )


class _StubNarrativeAnalyzer:
    def __init__(self, result_fn=None, exc: Exception | None = None, delay: float = 0.0):
        self.result_fn = result_fn
        self.exc = exc
        self.delay = delay
        self.calls = 0

    def analyze(self, script_input: Any) -> NarrativeAnalysisResult:
        self.calls += 1
        if self.delay > 0:
            time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result_fn(script_input)


class _StubVisualDirector:
    def __init__(self, result_fn=None, exc: Exception | None = None, delay: float = 0.0):
        self.result_fn = result_fn
        self.exc = exc
        self.delay = delay
        self.calls = 0

    def direct(self, narrative_analysis: Any, visual_language_override: Any = None) -> VisualDirectionResult:
        self.calls += 1
        if self.delay > 0:
            time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result_fn(narrative_analysis)


class _StubScenePlanner:
    def __init__(self, result_fn=None, exc: Exception | None = None, delay: float = 0.0):
        self.result_fn = result_fn
        self.exc = exc
        self.delay = delay
        self.calls = 0

    def plan_scenes(
        self,
        narrative_analysis: Any,
        visual_direction: Any,
        production_profile: Any,
        original_scenes: Any = None,
        planning_params: Any = None,
    ) -> ScenePlanResult:
        self.calls += 1
        if self.delay > 0:
            time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result_fn(narrative_analysis, visual_direction, production_profile)


# ---------------------------------------------------------------------------
# 9.1 Fluxo de sucesso
# ---------------------------------------------------------------------------


def test_01_success_flow_generates_valid_master_av_plan():
    script = _make_script(3)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))
    sp_stub = _StubScenePlanner(result_fn=lambda na, vd, prof: _make_plan_result(na, vd, prof))

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=sp_stub,
        timeout=10.0,
    )
    plan = director.create_master_plan(script, production_profile="PILOT_5MIN")

    assert isinstance(plan, MasterAVPlan)
    assert plan.production_profile == "PILOT_5MIN"
    assert plan.total_scenes == 3
    assert plan.total_duration_seconds == pytest.approx(30.0)
    assert plan.central_theme == "Sistemas ocultos"
    assert plan.visual_bible.visual_language == "documentary_realistic"
    assert [s.scene_id for s in plan.scenes] == [1, 2, 3]
    assert [s.narration for s in plan.scenes] == [s["narration"] for s in script["scenes"]]

    # Serialização JSON
    raw_json = plan.model_dump_json()
    parsed = json.loads(raw_json)
    assert parsed["plan_id"] == plan.plan_id
    assert parsed["total_scenes"] == 3

    # Compatibilidade Scene V2
    canonical = plan.to_canonical_scenes()
    assert len(canonical) == 3
    assert all(isinstance(c, Scene) for c in canonical)


def test_15_timeout_leaves_no_abandoned_background_threads():
    import threading

    script = _make_script(2)
    completed_after_return = threading.Event()
    threads_before = threading.active_count()

    class _SlowSyncAnalyzer:
        def analyze(self, script_data):
            time.sleep(0.15)
            completed_after_return.set()
            return _make_narrative_result(script_data)

    director = AIVideoDirector(
        narrative_analyzer=_SlowSyncAnalyzer(),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
        timeout=0.05,
    )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == "narrative_analyzer"
    assert exc_info.value.error_type == "TimeoutError"
    assert completed_after_return.is_set()
    assert threading.active_count() == threads_before


def test_16_native_component_timeout_propagated_and_mapped():
    script = _make_script(2)
    observed_timeouts = []

    class _TimeoutLLM:
        def complete(self, system_prompt: str = "", user_prompt: str = "", timeout: float = 30.0, **kwargs) -> str:
            observed_timeouts.append(timeout)
            raise TimeoutError(f"request timed out after {timeout}s")

    director = AIVideoDirector(llm_client=_TimeoutLLM(), timeout=0.05)

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert observed_timeouts == [0.05]
    assert exc_info.value.stage == "narrative_analyzer"
    assert exc_info.value.error_type == "TimeoutError"


@pytest.mark.parametrize("stage_name", ["narrative_analyzer", "visual_director", "scene_planner"])
def test_17_sync_client_without_timeout_rejected(stage_name):
    script = _make_script(2)

    class _NoTimeoutLLM:
        def complete(self, system_prompt: str = "", user_prompt: str = "") -> str:
            return "{}"

        def generate(self, prompt: str) -> str:
            return "{}"

    no_timeout_client = _NoTimeoutLLM()

    if stage_name == "narrative_analyzer":
        director = AIVideoDirector(
            narrative_analyzer=NarrativeAnalyzer(llm_client=no_timeout_client, timeout=1.0),
            visual_director=_StubVisualDirector(),
            scene_planner=_StubScenePlanner(),
        )
    elif stage_name == "visual_director":
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=VisualDirector(llm_client=no_timeout_client, timeout=1.0),
            scene_planner=_StubScenePlanner(),
        )
    else:
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
            scene_planner=ScenePlanner(llm_client=no_timeout_client, timeout=1.0),
        )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == stage_name
    assert "timeout" in str(exc_info.value).lower()


def test_18_async_client_timeout_enforced():
    import asyncio

    script = _make_script(2)

    class _AsyncSlowLLM:
        async def generate(self, prompt: str) -> str:
            await asyncio.sleep(0.3)
            return "{}"

    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
        visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
        scene_planner=ScenePlanner(llm_client=_AsyncSlowLLM(), timeout=0.05),
        timeout=0.05,
    )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == "scene_planner"
    assert exc_info.value.error_type == "TimeoutError"


@pytest.mark.parametrize("stage_name", ["narrative_analyzer", "visual_director", "scene_planner"])
def test_19_kwargs_only_sync_client_rejected(stage_name):
    script = _make_script(2)

    class _KwargsOnlyLLM:
        def complete(self, **kwargs) -> str:
            return "{}"

        def generate(self, prompt: str, **kwargs) -> str:
            return "{}"

    client = _KwargsOnlyLLM()

    if stage_name == "narrative_analyzer":
        director = AIVideoDirector(
            narrative_analyzer=NarrativeAnalyzer(llm_client=client, timeout=1.0),
            visual_director=_StubVisualDirector(),
            scene_planner=_StubScenePlanner(),
        )
    elif stage_name == "visual_director":
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=VisualDirector(llm_client=client, timeout=1.0),
            scene_planner=_StubScenePlanner(),
        )
    else:
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
            scene_planner=ScenePlanner(llm_client=client, timeout=1.0),
        )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == stage_name
    assert "timeout" in str(exc_info.value).lower()


def test_20_llm_client_deadline_stops_retries_and_propagates_remaining_time():
    from shared.llm_client import LLMClient

    client = LLMClient.__new__(LLMClient)
    client._gemini_client = object()
    client._deepseek_client = object()
    client._openai_client = None

    observed_timeouts = []

    def _slow_gemini(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=None):
        observed_timeouts.append(("gemini", timeout))
        time.sleep(0.12)
        raise RuntimeError("gemini transient failure")

    def _slow_openai(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, client=None, timeout=None):
        observed_timeouts.append(("deepseek", timeout))
        time.sleep(0.05)
        raise RuntimeError("deepseek transient failure")

    client._call_gemini = _slow_gemini
    client._call_openai = _slow_openai

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client.complete("sys", "usr", timeout=0.25)
    elapsed = time.monotonic() - start

    # Deve ter parado pelo deadline sem dormir os 2s de backoff do retry
    assert elapsed < 0.45
    assert len(observed_timeouts) >= 2
    assert observed_timeouts[0][1] <= 0.25
    assert observed_timeouts[1][1] < observed_timeouts[0][1]
    assert [c.id for c in canonical] == [1, 2, 3]
    assert [c.narration for c in canonical] == [s["narration"] for s in script["scenes"]]

    # Observabilidade
    assert len(plan.execution_metadata.stages) == 4
    assert all(st.status == "completed" for st in plan.execution_metadata.stages)
    assert plan.execution_metadata.total_duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# 9.2 Integridade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_script", [None, {}, {"scenes": []}, []])
def test_02_empty_or_invalid_script_rejected(bad_script):
    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(bad_script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "input_validation"


def test_03_invalid_profile_rejected():
    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(_make_script(2), production_profile="INVALID_PROFILE")
    assert exc_info.value.stage == "input_validation"
    assert exc_info.value.error_type == "InvalidProductionProfileError"


def test_04_duplicate_scene_id_in_input_rejected():
    script = {
        "scenes": [
            {"id": 1, "narration": "Cena um."},
            {"id": 1, "narration": "Cena um duplicada."},
        ]
    }
    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "input_validation"
    assert exc_info.value.error_type == "DuplicateSceneIdError"


def test_05_missing_scene_in_planner_output_rejected():
    script = _make_script(3)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))

    def _drop_last_scene(na, vd, prof):
        full = _make_plan_result(na, vd, prof)
        return ScenePlanResult(
            profile=prof,
            total_duration_seconds=20.0,
            scene_count=2,
            scenes=full.scenes[:2],
            visual_bible=full.visual_bible,
        )

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=_StubScenePlanner(result_fn=_drop_last_scene),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "scene_planner"
    assert exc_info.value.error_type == "SceneCountMismatchError"


def test_06_divergent_scene_id_order_rejected():
    script = _make_script(2)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))

    def _swap_ids(na, vd, prof):
        full = _make_plan_result(na, vd, prof)
        s1, s2 = full.scenes[0], full.scenes[1]
        s1_swapped = s1.model_copy(update={"scene_id": 2})
        s2_swapped = s2.model_copy(update={"scene_id": 1})
        return ScenePlanResult(
            profile=prof,
            total_duration_seconds=full.total_duration_seconds,
            scene_count=2,
            scenes=[s1_swapped, s2_swapped],
            visual_bible=full.visual_bible,
        )

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=_StubScenePlanner(result_fn=_swap_ids),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "scene_planner"
    assert exc_info.value.error_type == "SceneOrderOrIdMismatchError"


def test_07_altered_narration_rejected():
    script = _make_script(2)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))

    def _alter_narration(na, vd, prof):
        full = _make_plan_result(na, vd, prof)
        s1 = full.scenes[0].model_copy(update={"narration": "Texto alterado!"})
        return ScenePlanResult(
            profile=prof,
            total_duration_seconds=full.total_duration_seconds,
            scene_count=2,
            scenes=[s1, full.scenes[1]],
            visual_bible=full.visual_bible,
        )

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=_StubScenePlanner(result_fn=_alter_narration),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "master_plan_validation"
    assert exc_info.value.error_type == "NarrationAlteredError"


@pytest.mark.parametrize("bad_dur", [0.0, -5.0, float("nan"), float("inf")])
def test_08_invalid_duration_rejected(bad_dur):
    script = _make_script(2)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))

    def _bad_dur_plan(na, vd, prof):
        full = _make_plan_result(na, vd, prof)
        bad_scene = SimpleNamespace(
            scene_id=1,
            order=1,
            narration=script["scenes"][0]["narration"],
            duration_seconds=bad_dur,
        )
        return SimpleNamespace(
            profile=prof,
            total_duration_seconds=10.0,
            scene_count=2,
            scenes=[bad_scene, full.scenes[1]],
            visual_bible=full.visual_bible,
            validation_summary={},
        )

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=_StubScenePlanner(result_fn=_bad_dur_plan),
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage in {"scene_planner", "master_plan_validation"}


def test_09_missing_visual_bible_rejected():
    script = _make_script(2)
    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(
        result_fn=lambda na: SimpleNamespace(
            visual_bible=None,
            scene_directions=_make_visual_result(script).scene_directions,
        )
    )
    sp_stub = _StubScenePlanner(result_fn=lambda na, vd, prof: _make_plan_result(na, _make_visual_result(script), prof))

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=sp_stub,
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script, production_profile="PILOT_5MIN")
    assert exc_info.value.stage == "visual_director"


# ---------------------------------------------------------------------------
# 9.3 Falhas por etapa & Timeout & Sanitização
# ---------------------------------------------------------------------------


def test_10_stage_exceptions_preserve_stage_and_sanitize_secrets():
    script = _make_script(2)

    d1 = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(exc=RuntimeError("Auth failed sk-1234567890abcdef123456")),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
    )
    with pytest.raises(AIVideoDirectorError) as e1:
        d1.create_master_plan(script)
    assert e1.value.stage == "narrative_analyzer"
    assert "sk-1234567890abcdef" not in e1.value.message
    assert "sk-***" in e1.value.message

    d2 = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
        visual_director=_StubVisualDirector(exc=ValueError("Bad JSON")),
        scene_planner=_StubScenePlanner(),
    )
    with pytest.raises(AIVideoDirectorError) as e2:
        d2.create_master_plan(script)
    assert e2.value.stage == "visual_director"

    d3 = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
        visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
        scene_planner=_StubScenePlanner(exc=RuntimeError("Planner crashed")),
    )
    with pytest.raises(AIVideoDirectorError) as e3:
        d3.create_master_plan(script)
    assert e3.value.stage == "scene_planner"


def test_11_timeout_enforcement():
    script = _make_script(2)
    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(
            result_fn=lambda s: _make_narrative_result(s),
            delay=0.5,
        ),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
        timeout=0.05,
    )
    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)
    assert exc_info.value.stage == "narrative_analyzer"
    assert exc_info.value.error_type == "TimeoutError"


# ---------------------------------------------------------------------------
# 9.4 Imutabilidade & Determinismo
# ---------------------------------------------------------------------------


def test_12_immutability_and_deterministic_repeatability():
    script = _make_script(3)
    script_snapshot = copy.deepcopy(script)
    params = {"custom_flag": True}
    params_snapshot = copy.deepcopy(params)

    na_stub = _StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s))
    vd_stub = _StubVisualDirector(result_fn=lambda na: _make_visual_result(script))
    sp_stub = _StubScenePlanner(result_fn=lambda na, vd, prof: _make_plan_result(na, vd, prof))

    director = AIVideoDirector(
        narrative_analyzer=na_stub,
        visual_director=vd_stub,
        scene_planner=sp_stub,
    )

    plan1 = director.create_master_plan(script, production_profile="PILOT_5MIN", planning_params=params)
    plan2 = director.create_master_plan(script, production_profile="PILOT_5MIN", planning_params=params)

    assert script == script_snapshot
    assert params == params_snapshot

    assert plan1.production_profile == plan2.production_profile
    assert plan1.total_scenes == plan2.total_scenes
    assert plan1.total_duration_seconds == plan2.total_duration_seconds
    assert plan1.scenes == plan2.scenes
    assert plan1.visual_bible == plan2.visual_bible


# ---------------------------------------------------------------------------
# 9.5 Injeção de dependências & Cliente LLM compartilhado
# ---------------------------------------------------------------------------


def test_13_shared_llm_client_injection():
    shared_llm = object()
    director = AIVideoDirector(llm_client=shared_llm, timeout=15.0)
    assert director.narrative_analyzer.llm_client is shared_llm
    assert director.visual_director.llm_client is shared_llm
    assert director.scene_planner.llm_client is shared_llm
    assert director.timeout == 15.0


# ---------------------------------------------------------------------------
# 9.6 Teste de integração real (componentes reais com LLM determinístico)
# ---------------------------------------------------------------------------


def test_14_real_components_integration_with_deterministic_llm():
    """
    Executa NarrativeAnalyzer, VisualDirector e ScenePlanner reais através do
    AIVideoDirector, fornecendo um LLMClient determinístico sem chamadas pagas.
    """
    script = _make_script(3)

    class _DeterministicLLM:
        def __init__(self) -> None:
            self.call_count = 0

        def complete(self, system_prompt: str = "", user_prompt: str = "", timeout: float = 30.0, **kwargs) -> str:
            self.call_count += 1
            if self.call_count == 1:
                return json.dumps(_make_narrative_result(script).model_dump())
            if self.call_count == 2:
                return json.dumps(_make_visual_result(script).model_dump())
            return json.dumps({
                "scenes": [
                    {
                        "scene_id": s["id"],
                        "narrative_purpose": "hook" if s["id"] == 1 else "tension",
                        "duration_weight": 1.0,
                        "transition_in": "fade_black" if s["id"] == 1 else "cut",
                        "transition_out": "cut",
                    }
                    for s in script["scenes"]
                ]
            })

    director = AIVideoDirector(llm_client=_DeterministicLLM(), timeout=15.0)
    plan = director.create_master_plan(script, production_profile="PILOT_5MIN")

    assert isinstance(plan, MasterAVPlan)
    assert plan.total_scenes == 3
    assert plan.total_duration_seconds > 0
    assert [s.scene_id for s in plan.scenes] == [1, 2, 3]
    assert [s.narration for s in plan.scenes] == [s["narration"] for s in script["scenes"]]
    canonical = plan.to_canonical_scenes()
    assert len(canonical) == 3
    assert all(isinstance(c, Scene) for c in canonical)


def test_15_timeout_leaves_no_abandoned_background_threads():
    import threading

    script = _make_script(2)
    completed_after_return = threading.Event()
    threads_before = threading.active_count()

    class _SlowSyncAnalyzer:
        def analyze(self, script_data):
            time.sleep(0.15)
            completed_after_return.set()
            return _make_narrative_result(script_data)

    director = AIVideoDirector(
        narrative_analyzer=_SlowSyncAnalyzer(),
        visual_director=_StubVisualDirector(),
        scene_planner=_StubScenePlanner(),
        timeout=0.05,
    )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == "narrative_analyzer"
    assert exc_info.value.error_type == "TimeoutError"
    assert completed_after_return.is_set()
    assert threading.active_count() == threads_before


def test_16_native_component_timeout_propagated_and_mapped():
    script = _make_script(2)
    observed_timeouts = []

    class _TimeoutLLM:
        def complete(self, system_prompt: str = "", user_prompt: str = "", timeout: float = 30.0, **kwargs) -> str:
            observed_timeouts.append(timeout)
            raise TimeoutError(f"request timed out after {timeout}s")

    director = AIVideoDirector(llm_client=_TimeoutLLM(), timeout=0.05)

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert observed_timeouts == [0.05]
    assert exc_info.value.stage == "narrative_analyzer"
    assert exc_info.value.error_type == "TimeoutError"


@pytest.mark.parametrize("stage_name", ["narrative_analyzer", "visual_director", "scene_planner"])
def test_17_sync_client_without_timeout_rejected(stage_name):
    script = _make_script(2)

    class _NoTimeoutLLM:
        def complete(self, system_prompt: str = "", user_prompt: str = "") -> str:
            return "{}"

        def generate(self, prompt: str) -> str:
            return "{}"

    no_timeout_client = _NoTimeoutLLM()

    if stage_name == "narrative_analyzer":
        director = AIVideoDirector(
            narrative_analyzer=NarrativeAnalyzer(llm_client=no_timeout_client, timeout=1.0),
            visual_director=_StubVisualDirector(),
            scene_planner=_StubScenePlanner(),
        )
    elif stage_name == "visual_director":
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=VisualDirector(llm_client=no_timeout_client, timeout=1.0),
            scene_planner=_StubScenePlanner(),
        )
    else:
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
            scene_planner=ScenePlanner(llm_client=no_timeout_client, timeout=1.0),
        )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == stage_name
    assert "timeout" in str(exc_info.value).lower()


def test_18_async_client_timeout_enforced():
    import asyncio

    script = _make_script(2)

    class _AsyncSlowLLM:
        async def generate(self, prompt: str) -> str:
            await asyncio.sleep(0.3)
            return "{}"

    director = AIVideoDirector(
        narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
        visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
        scene_planner=ScenePlanner(llm_client=_AsyncSlowLLM(), timeout=0.05),
        timeout=0.05,
    )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == "scene_planner"
    assert exc_info.value.error_type == "TimeoutError"


@pytest.mark.parametrize("stage_name", ["narrative_analyzer", "visual_director", "scene_planner"])
def test_19_kwargs_only_sync_client_rejected(stage_name):
    script = _make_script(2)

    class _KwargsOnlyLLM:
        def complete(self, **kwargs) -> str:
            return "{}"

        def generate(self, prompt: str, **kwargs) -> str:
            return "{}"

    client = _KwargsOnlyLLM()

    if stage_name == "narrative_analyzer":
        director = AIVideoDirector(
            narrative_analyzer=NarrativeAnalyzer(llm_client=client, timeout=1.0),
            visual_director=_StubVisualDirector(),
            scene_planner=_StubScenePlanner(),
        )
    elif stage_name == "visual_director":
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=VisualDirector(llm_client=client, timeout=1.0),
            scene_planner=_StubScenePlanner(),
        )
    else:
        director = AIVideoDirector(
            narrative_analyzer=_StubNarrativeAnalyzer(result_fn=lambda s: _make_narrative_result(s)),
            visual_director=_StubVisualDirector(result_fn=lambda na: _make_visual_result(script)),
            scene_planner=ScenePlanner(llm_client=client, timeout=1.0),
        )

    with pytest.raises(AIVideoDirectorError) as exc_info:
        director.create_master_plan(script)

    assert exc_info.value.stage == stage_name
    assert "timeout" in str(exc_info.value).lower()


def test_20_llm_client_deadline_stops_retries_and_propagates_remaining_time():
    from shared.llm_client import LLMClient

    client = LLMClient.__new__(LLMClient)
    client._gemini_client = object()
    client._deepseek_client = object()
    client._openai_client = None

    observed_timeouts = []

    def _slow_gemini(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=None):
        observed_timeouts.append(("gemini", timeout))
        time.sleep(0.12)
        raise RuntimeError("gemini transient failure")

    def _slow_openai(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, client=None, timeout=None):
        observed_timeouts.append(("deepseek", timeout))
        time.sleep(0.05)
        raise RuntimeError("deepseek transient failure")

    client._call_gemini = _slow_gemini
    client._call_openai = _slow_openai

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client.complete("sys", "usr", timeout=0.25)
    elapsed = time.monotonic() - start

    # Deve ter parado pelo deadline sem dormir os 2s de backoff do retry
    assert elapsed < 0.45
    assert len(observed_timeouts) >= 2
    assert observed_timeouts[0][1] <= 0.25
    assert observed_timeouts[1][1] < observed_timeouts[0][1]
