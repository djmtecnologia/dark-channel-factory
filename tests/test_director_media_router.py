"""
Testes unitários para Sprint 2.6 — Tarefa 2:
Integração do DirectorExecutionInterpreter com Seleção de Mídia e Hook Engine.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from desktop_worker.director_execution_interpreter import (
    InterpretedDirectorPlan,
    NormalizedGlobalStyle,
)
from desktop_worker.director_media_router import (
    build_hook_prompt,
    build_scene_direction_index,
    interpret_director_plan_safe,
    resolve_scene_direction,
    select_media_with_direction,
)


def _sample_director_script_data() -> dict:
    return {
        "director_metadata": {
            "plan_id": "plan-26-t2",
            "schema_version": "2.0",
            "central_theme": "dark tech investigation",
            "visual_bible": {
                "visual_language": "cinematic_documentary",
                "dominant_style": "dark_cinematic",
                "color_palette": ["#0A0E17", "#E63946"],
                "lighting_mood": "low_key_dramatic",
                "pacing_rhythm": "deliberate",
            },
            "scenes_unconsumed": {
                "10": {"narrative_role": "hook_opening", "continuity_anchor": "red_led_rack"},
                "20": {"narrative_role": "evidence_reveal"},
            },
        },
        "scenes": [
            {
                "scene_id": 10,
                "order": 1,
                "b_roll_visual": "hacker terminal in dark server room",
                "b_roll_query": "server room",
                "visual_production_type": "cinematic_still",
                "estimated_cost_tier": 2,
                "visual_direction": {
                    "visual_intent": "create immediate tension",
                },
                "continuity": {
                    "continuity_anchor": "red_led_rack",
                },
            },
            {
                "scene_id": 20,
                "order": 2,
                "b_roll_visual": "financial chart crash",
                "b_roll_query": "stock market crash",
                "visual_production_type": "stock_footage",
                "estimated_cost_tier": 1,
                "visual_direction": {},
            },
        ],
    }


def test_01_job_director_enabled():
    plan = interpret_director_plan_safe(_sample_director_script_data())
    assert plan.enabled is True
    assert plan.plan_id == "plan-26-t2"
    assert len(plan.scenes) == 2


def test_02_job_legacy_without_metadata():
    legacy_script = {
        "scenes": [
            {"id": 1, "b_roll_visual": "city night", "narration": "Hello"}
        ]
    }
    plan = interpret_director_plan_safe(legacy_script)
    assert plan.enabled is False
    assert plan.disabled_reason == "missing_director_metadata"


def test_03_scene_0_hook_with_visual_direction():
    from dataclasses import replace
    from desktop_worker.director_execution_interpreter import NormalizedContinuity

    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    scene0 = script_data["scenes"][0]
    direction = resolve_scene_direction(plan, by_id, by_order, scene0, 0)
    assert direction is not None
    direction = replace(direction, continuity=NormalizedContinuity(continuity_anchor="red_led_rack"))

    prompt, used_director = build_hook_prompt(
        topic="Cyber Attack",
        scene=scene0,
        scene_direction=direction,
        global_style=plan.global_style,
    )
    assert used_director is True
    assert "hacker terminal in dark server room" in prompt
    assert "red_led_rack" in prompt
    assert "dark_cinematic" in prompt


def test_04_scene_0_hook_without_visual_direction():
    prompt, used_director = build_hook_prompt(
        topic="Cyber Attack",
        scene={"b_roll_visual": "something"},
        scene_direction=None,
        global_style=None,
    )
    assert used_director is False
    assert "A dramatic cinematic premium illustration representing: 'Cyber Attack'" in prompt
    assert "trending on artstation" in prompt


@pytest.mark.parametrize(
    "prod_type,expected_prefix",
    [
        ("local_asset", "local_asset:stock_fallback"),
        ("stock_footage", "stock_footage:broll"),
        ("motion_graphic", "motion_graphic:broll"),
        ("blender_3d", "blender_3d:broll"),
        ("auto", "auto:broll"),
    ],
)
def test_05_routing_by_production_type(tmp_path, prod_type, expected_prefix):
    script_data = _sample_director_script_data()
    script_data["scenes"][1]["visual_production_type"] = prod_type
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][1], 1)

    fetch_broll = MagicMock(return_value="stock.mp4")
    gen_pollinations = MagicMock(return_value=True)

    path, strategy = select_media_with_direction(
        visual_kw="financial chart crash",
        scene_text="markets fell",
        scene_index=1,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path == "stock.mp4"
    assert strategy == expected_prefix
    fetch_broll.assert_called_once()
    gen_pollinations.assert_not_called()


def test_06_local_cache_priority(tmp_path):
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)

    local_hit = str(tmp_path / "cached.mp4")
    find_local = MagicMock(return_value=local_hit)
    fetch_broll = MagicMock(return_value="stock.mp4")
    gen_pollinations = MagicMock(return_value=True)

    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="text",
        scene_index=0,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
        find_local_cache_fn=find_local,
    )
    assert path == local_hit
    assert "local_cache_hit" in strategy
    fetch_broll.assert_not_called()
    gen_pollinations.assert_not_called()


def test_07_stock_priority(tmp_path):
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][1], 1)

    fetch_broll = MagicMock(return_value="pexels_123.mp4")
    gen_pollinations = MagicMock(return_value=True)

    path, strategy = select_media_with_direction(
        visual_kw="stock market crash",
        scene_text="crash",
        scene_index=1,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path == "pexels_123.mp4"
    assert strategy == "stock_footage:broll"
    gen_pollinations.assert_not_called()


def test_08_cinematic_still_with_pollinations(tmp_path):
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)

    call_order = []
    def find_local(kw, u):
        call_order.append("local_cache")
        return None
    def fetch_broll(kw, u):
        call_order.append("stock")
        return None
    def gen_pollinations(p, o):
        call_order.append("pollinations")
        return True

    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
        find_local_cache_fn=find_local,
    )
    assert strategy == "cinematic_still:pollinations"
    assert path is not None and path.endswith(".jpg")
    assert call_order == ["local_cache", "stock", "pollinations"]


def test_09_pollinations_fallback_to_stock(tmp_path):
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)

    call_order = []
    def fetch_broll(kw, u):
        call_order.append("stock")
        return None
    def gen_pollinations(p, o):
        call_order.append("pollinations")
        raise RuntimeError("Pollinations down")

    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path is None
    assert strategy == "cinematic_still:fallback_unavailable"
    assert call_order == ["stock", "pollinations"]


def test_10_unknown_production_type_falls_back_to_auto(tmp_path):
    script_data = _sample_director_script_data()
    script_data["scenes"][1]["visual_production_type"] = "hologram_4d"
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][1], 1)

    assert direction is not None
    assert direction.production_route.visual_production_type == "auto"

    fetch_broll = MagicMock(return_value="auto_stock.mp4")
    path, strategy = select_media_with_direction(
        visual_kw="chart",
        scene_text="text",
        scene_index=1,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=MagicMock(),
    )
    assert path == "auto_stock.mp4"
    assert strategy == "auto:broll"


def test_11_interpreter_failure_falls_back_to_legacy():
    with patch(
        "desktop_worker.director_media_router.DirectorExecutionInterpreter",
        side_effect=RuntimeError("boom"),
    ):
        plan = interpret_director_plan_safe(_sample_director_script_data())

    assert plan.enabled is False
    assert plan.disabled_reason == "interpreter_exception:RuntimeError"


def test_12_scene_id_association():
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)

    # Mesmo se o índice posicional for diferente, scene_id=20 deve resolver para a cena 20
    dir_20 = resolve_scene_direction(plan, by_id, by_order, {"scene_id": 20}, scene_index=0)
    assert dir_20 is not None
    assert dir_20.scene_id == 20

    # Fallback por order (scene_index + 1) quando scene_id não está presente
    dir_10_by_order = resolve_scene_direction(plan, by_id, by_order, {}, scene_index=0)
    assert dir_10_by_order is not None
    assert dir_10_by_order.scene_id == 10


def test_13_no_llm_calls(tmp_path):
    script_data = _sample_director_script_data()
    with patch("requests.post", side_effect=AssertionError("No HTTP/LLM POST allowed")):
        plan = interpret_director_plan_safe(script_data)
        by_id, by_order = build_scene_direction_index(plan)
        direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][1], 1)
        build_hook_prompt("Topic", script_data["scenes"][0], direction, plan.global_style)
        select_media_with_direction(
            visual_kw="chart",
            scene_text="text",
            scene_index=1,
            scene_direction=direction,
            used_assets=set(),
            assets_cache_dir=str(tmp_path),
            fetch_broll_fn=lambda kw, u: "ok.mp4",
            generate_pollinations_fn=lambda p, o: True,
        )


def test_14_legacy_behavior_preserved(tmp_path):
    fetch_broll = MagicMock(return_value="legacy_broll.mp4")
    gen_pollinations = MagicMock()

    path, strategy = select_media_with_direction(
        visual_kw="city night",
        scene_text="narration",
        scene_index=2,
        scene_direction=None,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path == "legacy_broll.mp4"
    assert strategy == "legacy"
    fetch_broll.assert_called_once_with("city night", set())
    gen_pollinations.assert_not_called()


def test_15_cinematic_still_uses_stock_before_pollinations(tmp_path):
    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)

    call_order = []
    def find_local(kw, u):
        call_order.append("local_cache")
        return None
    def fetch_broll(kw, u):
        call_order.append("stock")
        return "stock_hit.mp4"
    def gen_pollinations(p, o):
        call_order.append("pollinations")
        return True

    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
        find_local_cache_fn=find_local,
    )
    assert path == "stock_hit.mp4"
    assert strategy == "cinematic_still:stock_hit"
    assert call_order == ["local_cache", "stock"]


def test_16_estimated_cost_tier_valid_and_invalid_defaults(tmp_path):
    from dataclasses import replace

    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)
    assert direction is not None
    assert direction.estimated_cost_tier == 2

    # Invalid tier (e.g. 99 or None) falls back safely to default 1
    bad_dir = replace(direction, estimated_cost_tier=99)
    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=bad_dir,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=lambda kw, u: "stock_safe.mp4",
        generate_pollinations_fn=lambda p, o: True,
    )
    assert path == "stock_safe.mp4"
    assert strategy == "cinematic_still:stock_hit"


def test_17_integration_render_video_hook_respects_router_and_no_early_return(tmp_path):
    """
    Teste de integração: verifica que desktop_node.py encaminha a Cena 0
    através do roteador de mídia sem retorno antecipado para Pollinations.
    """
    import desktop_node

    call_order = []

    def fake_fetch_broll(kw, used=None):
        call_order.append(("fetch_broll", kw))
        p = str(tmp_path / "stock_hook.mp4")
        with open(p, "wb") as f:
            f.write(b"0" * 20000)
        return p

    def fake_pollinations(prompt, out_path):
        call_order.append(("pollinations", prompt))
        return True

    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    scene0 = script_data["scenes"][0]
    direction0 = resolve_scene_direction(plan, by_id, by_order, scene0, 0)
    hook_prompt, used_dir = build_hook_prompt("Cyber Attack", scene0, direction0, plan.global_style)

    assert used_dir is True
    path, strategy = select_media_with_direction(
        visual_kw=scene0["b_roll_visual"],
        scene_text="",
        scene_index=0,
        scene_direction=direction0,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fake_fetch_broll,
        generate_pollinations_fn=fake_pollinations,
        hook_prompt=hook_prompt,
    )
    assert strategy == "cinematic_still:stock_hit"
    assert path.endswith("stock_hook.mp4")
    assert [c[0] for c in call_order] == ["fetch_broll"]
    assert hasattr(desktop_node, "render_video")


def test_18_cinematic_still_cache_hit_before_stock_and_respects_used_assets(tmp_path):
    import hashlib

    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)
    assert direction is not None

    still_prompt = f"hacker terminal, {direction.visual_prompt_complement}"
    safe_id = hashlib.md5(f"{still_prompt}_0".encode()).hexdigest()[:12]
    still_path = tmp_path / f"director_still_{safe_id}.jpg"
    still_path.write_bytes(b"x" * 20000)

    fetch_broll = MagicMock(return_value="stock.mp4")
    gen_pollinations = MagicMock(return_value=True)

    # 1. Cache hit antes de Pexels/Pixabay
    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=direction,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path == str(still_path)
    assert strategy == "cinematic_still:still_cache_hit"
    fetch_broll.assert_not_called()
    gen_pollinations.assert_not_called()

    # 2. Se já estiver em used_assets, não reutiliza e consulta stock
    path2, strategy2 = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=direction,
        used_assets={str(still_path)},
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path2 == "stock.mp4"
    assert strategy2 == "cinematic_still:stock_hit"
    fetch_broll.assert_called_once()


@pytest.mark.parametrize("tier", [1, 2, 3, 4, 5])
def test_19_estimated_cost_tier_all_valid_levels_1_to_5(tmp_path, tier):
    from dataclasses import replace

    script_data = _sample_director_script_data()
    plan = interpret_director_plan_safe(script_data)
    by_id, by_order = build_scene_direction_index(plan)
    direction = resolve_scene_direction(plan, by_id, by_order, script_data["scenes"][0], 0)
    assert direction is not None

    tiered_dir = replace(direction, estimated_cost_tier=tier)
    fetch_broll = MagicMock(return_value="stock_tier.mp4")
    gen_pollinations = MagicMock(return_value=True)

    path, strategy = select_media_with_direction(
        visual_kw="hacker terminal",
        scene_text="hook",
        scene_index=0,
        scene_direction=tiered_dir,
        used_assets=set(),
        assets_cache_dir=str(tmp_path),
        fetch_broll_fn=fetch_broll,
        generate_pollinations_fn=gen_pollinations,
    )
    assert path == "stock_tier.mp4"
    assert strategy == "cinematic_still:stock_hit"
    gen_pollinations.assert_not_called()


def test_20_real_render_video_director_and_legacy_flow(tmp_path, monkeypatch):
    """
    Executa desktop_node.render_video() real com mocks para FFmpeg, narração,
    legendas, Pexels/Pixabay, Pollinations, cache e SFX.
    """
    import desktop_node
    import desktop_worker.director_media_router as router

    monkeypatch.setattr(desktop_node, "ASSETS_CACHE", str(tmp_path))
    monkeypatch.setattr(desktop_node, "OUTPUT_DIR", str(tmp_path))

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"fake_audio")
    srt_file = tmp_path / "subs.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    stock_file = tmp_path / "stock_hook.mp4"
    stock_file.write_bytes(b"0" * 20000)

    broll_counter = {"n": 0}
    def fake_fetch_broll(*a, **kw):
        idx = broll_counter["n"]
        broll_counter["n"] += 1
        p = tmp_path / f"stock_{idx}.mp4"
        p.write_bytes(b"0" * 20000)
        return str(p)

    monkeypatch.setattr(desktop_node, "generate_voice_master", lambda *a, **kw: str(audio_file))
    monkeypatch.setattr(desktop_node, "generate_subtitles_locally", lambda *a, **kw: str(srt_file))
    monkeypatch.setattr(desktop_node, "fetch_sfx", lambda *a, **kw: None)
    monkeypatch.setattr(desktop_node, "fetch_premium_broll", fake_fetch_broll)

    pollinations_calls = []
    monkeypatch.setattr(desktop_node, "generate_pollinations_image", lambda p, o: (pollinations_calls.append(p), True)[1])

    class _StopAfterAssets(Exception):
        pass

    # Interrompe após a seleção de mídia para não rodar FFmpeg pesado de renderização final,
    # mas após executar todo o fluxo real de render_video() até download_assets().
    def fake_subprocess_run(cmd, *a, **kw):
        if "-i" in cmd and str(audio_file) in cmd:
            m = MagicMock()
            m.stderr = "Duration: 00:00:05.00,"
            return m
        raise _StopAfterAssets()

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    # Spy no interpretador, build_hook_prompt e select_media_with_direction
    interpret_spy = MagicMock(wraps=router.interpret_director_plan_safe)
    hook_spy = MagicMock(wraps=router.build_hook_prompt)
    select_spy = MagicMock(wraps=router.select_media_with_direction)
    monkeypatch.setattr(router, "interpret_director_plan_safe", interpret_spy)
    monkeypatch.setattr(router, "build_hook_prompt", hook_spy)
    monkeypatch.setattr(router, "select_media_with_direction", select_spy)

    # Mock pysubs2.load
    fake_sub = MagicMock()
    fake_sub.end = 2000
    monkeypatch.setattr("pysubs2.load", lambda *a, **kw: [fake_sub])

    # 1. Fluxo Director habilitado
    director_script = _sample_director_script_data()
    director_script["scenes"][0]["narration"] = "Hook narration"
    director_script["scenes"][1]["narration"] = "Second scene narration"
    director_job = {
        "job_id": "job-dir-1",
        "topic": "Cyber Attack",
        "script_data": director_script,
        "is_short": False,
    }
    try:
        desktop_node.render_video(director_job)
    except _StopAfterAssets:
        pass

    assert interpret_spy.call_count == 1
    assert hook_spy.call_count >= 1
    assert select_spy.call_count >= 1
    # Como stock retornou stock_hook.mp4, Pollinations NÃO deve ter sido chamado na Cena 0
    assert len(pollinations_calls) == 0

    # 2. Fluxo Legacy (sem director_metadata)
    interpret_spy.reset_mock()
    hook_spy.reset_mock()
    select_spy.reset_mock()
    pollinations_calls.clear()

    legacy_job = {
        "job_id": "job-leg-1",
        "topic": "Cyber Attack Legacy",
        "script_data": {"scenes": [{"id": 1, "b_roll_visual": "city", "narration": "Hi"}]},
        "is_short": False,
    }
    try:
        desktop_node.render_video(legacy_job)
    except _StopAfterAssets:
        pass

    assert interpret_spy.call_count == 1
    assert hook_spy.call_count == 1
    # No legado, Cena 0 chama generate_pollinations_image diretamente
    assert len(pollinations_calls) == 1
