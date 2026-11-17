"""
Testes unitários para DirectorExecutionInterpreter — Sprint 2.6 (Tarefa 1).
Sem rede, sem LLM e sem renderização FFmpeg.
"""
import pytest

from desktop_worker.director_execution_interpreter import (
    VALID_CAMERA_MOVEMENTS,
    VALID_VISUAL_PRODUCTION_TYPES,
    CameraMovementMapper,
    DirectorExecutionInterpreter,
    NormalizedGlobalStyle,
    VisualProductionRouter,
    build_visual_prompt_complement,
)


def _sample_script_data():
    return {
        "title": "The Hidden Architecture",
        "script_full": "Full narration.",
        "scenes": [
            {
                "id": 0,
                "scene_id": 0,
                "order": 1,
                "narration": "In the beginning, silence.",
                "duration_s": 5.0,
                "b_roll_visual": "Dark brutalist monolith in fog",
                "b_roll_query": "dark monolith fog",
                "visual_production_type": "cinematic_still",
                "camera_framing": "extreme_wide",
                "camera_movement": "slow_push_in",
                "lighting_mood": "chiaroscuro_amber",
                "transition_in": "fade_in",
                "transition_out": "crossfade_slow",
                "continuity_anchor": "obsidian monolith",
                "on_screen_text": "CHAPTER I",
                "estimated_cost_tier": 2,
            }
        ],
        "director_metadata": {
            "plan_id": "avplan_abc123",
            "schema_version": "2.4.0",
            "production_profile": "documentary_dark",
            "central_theme": "Hidden Power",
            "visual_bible": {
                "visual_language": "cinematic_documentary",
                "dominant_style": "noir_architectural",
                "color_palette": ["obsidian black", "amber gold"],
                "lighting_mood": "low_key_dramatic",
                "pacing_rhythm": "measured_tension",
            },
            "scenes_unconsumed": {
                "0": {
                    "scene_id": 0,
                    "narrative_role": "opening_hook",
                    "visual_intent": "Establish scale and mystery",
                    "continuity_notes": {"location": "void_plain", "time": "dusk"},
                    "visual_direction_full": {
                        "depth_of_field": "shallow_focus",
                        "composition": "central_symmetry",
                        "atmosphere": "ominous_stillness",
                        "color_palette_notes": "cold slate with amber rim",
                        "what_viewer_sees": "A towering monolith",
                        "dominant_element": "monolith silhouette",
                        "visual_connection_previous": None,
                        "visual_connection_next": "amber light pulse",
                    },
                }
            },
        },
        "master_av_plan_summary": {
            "plan_id": "avplan_abc123",
            "schema_version": "2.4.0",
            "production_profile": "documentary_dark",
            "total_scenes": 1,
            "total_duration_seconds": 5.0,
        },
    }


def test_1_interpret_valid_visual_bible():
    interpreter = DirectorExecutionInterpreter()
    plan = interpreter.interpret(_sample_script_data())

    assert plan.enabled is True
    assert plan.plan_id == "avplan_abc123"
    assert plan.schema_version == "2.4.0"
    assert plan.global_style.visual_language == "cinematic_documentary"
    assert plan.global_style.dominant_style == "noir_architectural"
    assert plan.global_style.color_palette == ("obsidian black", "amber gold")
    assert plan.global_style.lighting_mood == "low_key_dramatic"
    assert plan.global_style.pacing_rhythm == "measured_tension"
    assert plan.global_style.central_theme == "Hidden Power"
    assert plan.global_style.production_profile == "documentary_dark"


def test_2_interpret_scene_visual_direction():
    interpreter = DirectorExecutionInterpreter()
    plan = interpreter.interpret(_sample_script_data())

    assert len(plan.scenes) == 1
    s = plan.scenes[0]
    assert s.scene_id == 0
    assert s.order == 1
    assert s.camera_framing == "extreme_wide"
    assert s.camera_movement.movement == "slow_push_in"
    assert s.depth_of_field == "shallow_focus"
    assert s.composition == "central_symmetry"
    assert s.lighting_mood == "chiaroscuro_amber"
    assert s.atmosphere == "ominous_stillness"
    assert s.color_palette_notes == "cold slate with amber rim"
    assert s.production_route.visual_production_type == "cinematic_still"
    assert s.estimated_cost_tier == 2
    assert s.narrative_role == "opening_hook"
    assert s.visual_intent == "Establish scale and mystery"
    assert s.dominant_element == "monolith silhouette"
    assert s.transition_in == "fade_in"
    assert s.transition_out == "crossfade_slow"
    assert s.on_screen_text == "CHAPTER I"


def test_3_interpret_continuity():
    interpreter = DirectorExecutionInterpreter()
    plan = interpreter.interpret(_sample_script_data())
    c = plan.scenes[0].continuity

    assert c.continuity_anchor == "obsidian monolith"
    assert c.visual_connection_previous is None
    assert c.visual_connection_next == "amber light pulse"
    assert c.continuity_notes == {"location": "void_plain", "time": "dusk"}


@pytest.mark.parametrize("movement", sorted(VALID_CAMERA_MOVEMENTS))
def test_4_map_all_known_camera_movements(movement):
    mapper = CameraMovementMapper()
    inst = mapper.map_movement(movement)
    assert inst.movement == movement
    assert inst.fallback_applied is False


def test_5_unknown_camera_movement_defaults_to_static():
    mapper = CameraMovementMapper()
    inst = mapper.map_movement("warp_speed_spin")
    assert inst.movement == "static"
    assert inst.kind == "static"
    assert inst.fallback_applied is True


def test_6_missing_optional_fields_use_safe_defaults():
    data = {
        "scenes": [{"id": 1, "order": 1, "b_roll_visual": "City street"}],
        "director_metadata": {"visual_bible": {}},
    }
    plan = DirectorExecutionInterpreter().interpret(data)
    assert plan.enabled is True
    assert len(plan.scenes) == 1
    s = plan.scenes[0]
    assert s.camera_framing == "wide"
    assert s.camera_movement.movement == "static"
    assert s.production_route.visual_production_type == "auto"
    assert s.on_screen_text is None
    assert s.continuity.continuity_anchor is None


def test_7_incomplete_metadata_handled_safely():
    data = {
        "scenes": [{"id": 2}],
        "director_metadata": {},
    }
    plan = DirectorExecutionInterpreter().interpret(data)
    assert plan.enabled is True
    assert plan.global_style.dominant_style == "dark_cinematic"
    assert len(plan.scenes) == 1


@pytest.mark.parametrize("bad_input", [None, "not_a_dict", 123, []])
def test_8_invalid_structures_return_safe_disabled_state(bad_input):
    plan = DirectorExecutionInterpreter().interpret(bad_input)
    assert plan.enabled is False
    assert plan.disabled_reason == "invalid_script_data_type"


def test_8b_invalid_director_metadata_type():
    plan = DirectorExecutionInterpreter().interpret({"director_metadata": "corrupted"})
    assert plan.enabled is False
    assert plan.disabled_reason == "invalid_director_metadata_type"


def test_9_unknown_fields_do_not_break_execution():
    data = _sample_script_data()
    data["director_metadata"]["future_field"] = {"nested": 42}
    data["scenes"][0]["unknown_extra"] = "ignored"
    plan = DirectorExecutionInterpreter().interpret(data)
    assert plan.enabled is True
    assert len(plan.scenes) == 1


def test_10_and_11_visual_prompt_complement_preserves_original_and_avoids_duplication():
    style = NormalizedGlobalStyle(
        dominant_style="noir_architectural",
        color_palette=("obsidian black", "amber gold"),
        lighting_mood="low_key_dramatic",
    )
    original = "Dark brutalist monolith in fog, noir_architectural"
    comp = build_visual_prompt_complement(
        original_prompt=original,
        global_style=style,
        lighting_mood="chiaroscuro_amber",
        atmosphere="ominous_stillness",
        color_palette_notes="cold slate",
        visual_intent="Establish scale",
        dominant_element="monolith silhouette",
        continuity_anchor="obsidian monolith",
    )
    assert "noir_architectural" not in comp
    assert "chiaroscuro_amber" in comp
    assert "obsidian monolith" in comp
    assert original == "Dark brutalist monolith in fog, noir_architectural"


def test_12_determinism_same_input_same_output():
    interpreter = DirectorExecutionInterpreter()
    data = _sample_script_data()
    p1 = interpreter.interpret(data)
    p2 = interpreter.interpret(data)
    assert p1 == p2


def test_13_no_external_calls_in_module(monkeypatch):
    import urllib.request
    import socket

    def _forbid(*args, **kwargs):
        raise AssertionError("External network call is forbidden")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid)
    monkeypatch.setattr(socket, "create_connection", _forbid)

    plan = DirectorExecutionInterpreter().interpret(_sample_script_data())
    assert plan.enabled is True


def test_14_legacy_job_without_director_metadata():
    legacy_script = {
        "title": "Legacy Video",
        "script_full": "Hello world",
        "scenes": [{"id": 0, "narration": "Hello", "b_roll_visual": "Sky"}],
    }
    plan = DirectorExecutionInterpreter().interpret(legacy_script)
    assert plan.enabled is False
    assert plan.disabled_reason == "missing_director_metadata"
    assert plan.scenes == ()


@pytest.mark.parametrize("prod_type", sorted(VALID_VISUAL_PRODUCTION_TYPES))
def test_visual_production_router_known_types(prod_type):
    route = VisualProductionRouter().normalize_route(prod_type)
    assert route.visual_production_type == prod_type
    assert route.fallback_applied is False


def _sample_script_data_with_nested_continuity():
    data = _sample_script_data()
    data["director_metadata"]["scenes_unconsumed"]["0"]["continuity_notes"] = {
        "location": "void_plain",
        "nested_dict": {"lighting": "amber", "intensity": 0.8},
        "nested_list": ["monolith", {"prop": "lantern"}],
    }
    return data


def test_15_immutability_continuity_notes_top_level_key():
    plan = DirectorExecutionInterpreter().interpret(_sample_script_data_with_nested_continuity())
    notes = plan.scenes[0].continuity.continuity_notes
    with pytest.raises(TypeError):
        notes["location"] = "changed"


def test_16_immutability_nested_dict():
    plan = DirectorExecutionInterpreter().interpret(_sample_script_data_with_nested_continuity())
    nested = plan.scenes[0].continuity.continuity_notes["nested_dict"]
    with pytest.raises(TypeError):
        nested["lighting"] = "blue"


def test_17_immutability_nested_list():
    plan = DirectorExecutionInterpreter().interpret(_sample_script_data_with_nested_continuity())
    nested_list = plan.scenes[0].continuity.continuity_notes["nested_list"]
    assert isinstance(nested_list, tuple)
    with pytest.raises(AttributeError):
        nested_list.append("extra")
    with pytest.raises(TypeError):
        nested_list[1]["prop"] = "sword"


def test_18_original_input_remains_intact():
    import copy

    original = _sample_script_data_with_nested_continuity()
    snapshot = copy.deepcopy(original)
    plan = DirectorExecutionInterpreter().interpret(original)

    assert plan.enabled is True
    assert original == snapshot
    # Mutar a entrada original após interpretar não afeta o plano já retornado
    original["director_metadata"]["scenes_unconsumed"]["0"]["continuity_notes"]["location"] = "mutated"
    assert plan.scenes[0].continuity.continuity_notes["location"] == "void_plain"


def test_19_determinism_with_nested_continuity():
    data = _sample_script_data_with_nested_continuity()
    p1 = DirectorExecutionInterpreter().interpret(data)
    p2 = DirectorExecutionInterpreter().interpret(data)
    assert p1 == p2
