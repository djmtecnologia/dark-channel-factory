"""Sprint 2.6 — Tarefa 4: Regressão Integrada e Homologação Final.

Valida toda a cadeia de produção do Pipeline B:
Master AV Plan -> MasterPlanAdapter -> Cloud Hub -> Desktop Node (render_video) ->
DirectorExecutionInterpreter -> Director Media Router -> Director Camera Engine ->
FFmpeg -> vídeo final, além de regressão Legacy, isolamento multitenant e observabilidade.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
from shared.models import (
    CameraFraming,
    CameraMovement,
    Emotion,
    NarrativePurpose,
    SceneType,
    VisualProductionType,
)
from agents.video_director.pipeline_adapter import MasterPlanAdapter
from cloud_hub.core.video_director_integration import integrate_video_director
from cloud_hub.core.video_director_observability import (
    VideoDirectorObservabilityCollector,
)
from desktop_worker.director_camera_engine import resolve_scene_camera_filter
from desktop_worker.director_execution_interpreter import DirectorExecutionInterpreter
from desktop_worker.director_media_router import (
    build_hook_prompt,
    build_scene_direction_index,
    interpret_director_plan_safe,
    resolve_scene_direction,
    select_media_with_direction,
)
from tests.test_director_camera_engine import _assert_no_black_borders


# ---------------------------------------------------------------------------
# Helpers sintéticos (sem dependências externas)
# ---------------------------------------------------------------------------

def _build_master_plan_5_scenes(plan_id: str = "avplan_e2e_001") -> MasterAVPlan:
    vb = VisualBible(
        visual_language=VisualLanguage.DOCUMENTARY_REALISTIC,
        dominant_style="cinematic documentary",
        color_palette=["#0B132B", "#1C2541", "#5BC0BE"],
        lighting_mood="low-key chiaroscuro",
        pacing_rhythm="measured_tension",
    )

    specs = [
        (
            1,
            CameraMovement.SLOW_PUSH_IN,
            VisualProductionType.CINEMATIC_STILL,
            "Radar dish silhouetted at dusk.",
        ),
        (
            2,
            CameraMovement.TRACKING_LATERAL,
            VisualProductionType.CINEMATIC_STILL,
            "Engineers inspecting telemetry tape.",
        ),
        (
            3,
            CameraMovement.CRANE_SWEEP,  # normalizado para static
            VisualProductionType.CINEMATIC_STILL,
            "Towering antenna against storm clouds.",
        ),
        (
            4,
            CameraMovement.DRAMATIC_PULL_OUT,
            VisualProductionType.STOCK_FOOTAGE,
            "Control room monitors flashing.",
        ),
        (
            5,
            CameraMovement.STATIC,
            VisualProductionType.CINEMATIC_STILL,
            "Declassified folder on wooden desk.",
        ),
    ]

    scenes: List[PlannedScene] = []
    for order, cam_mov, prod_type, desc in specs:
        t0 = (order - 1) * 2.5
        t1 = order * 2.5
        narration = f"Narration for sc_{order:03d}: {desc}"
        scenes.append(
            PlannedScene(
                scene_id=order,
                order=order,
                narration=narration,
                narrative_role="opening_hook" if order == 1 else "context",
                narrative_purpose=NarrativePurpose.HOOK,
                visual_intent=desc,
                duration_seconds=2.5,
                start_time=t0,
                end_time=t1,
                timestamp=f"0:0{int(t0)}-0:0{int(t1)}",
                visual_direction=SceneVisualDirection(
                    scene_id=order,
                    narration=narration,
                    what_viewer_sees=desc,
                    dominant_element="Radar facility",
                    visual_production_type=prod_type,
                    camera_framing=CameraFraming.MEDIUM,
                    camera_movement=cam_mov,
                    b_roll_visual=desc,
                    b_roll_query=f"radar sc_{order:03d}",
                    sfx_cue="ambient_hum",
                    emotion=Emotion.TRUST,
                    scene_type=SceneType.HOOK if order == 1 else SceneType.PROBLEM,
                    continuity_anchor="radar_station_01_dusk",
                ),
                extra_fields={"visual_cues": [{"cue": f"cue_sc_{order:03d}"}]},
            )
        )

    exec_meta = {
        "total_duration_seconds": 0.8,
        "production_profile": "PILOT_5MIN",
        "stages": [],
        "components": {"scene_planner": "ScenePlanner"},
        "warnings": [],
    }

    return MasterAVPlan(
        plan_id=plan_id,
        schema_version="2.4.0",
        production_profile="PILOT_5MIN",
        central_theme="The 1999 Radar Anomaly",
        visual_bible=vb,
        total_scenes=5,
        total_duration_seconds=12.5,
        scenes=scenes,
        validation_summary={"passed": True},
        execution_metadata=exec_meta,
    )


def _create_patterned_image(path: Path, bg_rgb: tuple, accent_rgb: tuple, label: str) -> None:
    """Gera imagem sintética com grade e formas geométricas para detectar movimento."""
    img = Image.new("RGB", (1280, 720), bg_rgb)
    draw = ImageDraw.Draw(img)
    for x in range(0, 1280, 80):
        draw.line([(x, 0), (x, 720)], fill=accent_rgb, width=2)
    for y in range(0, 720, 80):
        draw.line([(0, y), (1280, y)], fill=accent_rgb, width=2)
    draw.rectangle([440, 220, 840, 500], outline=(255, 255, 255), width=6)
    draw.ellipse([580, 300, 700, 420], fill=accent_rgb)
    # Elemento visual assimétrico isolado (verde puro) para medição direcional sem contaminação
    draw.ellipse([520, 280, 620, 380], fill=(20, 240, 20))
    draw.text((40, 40), label, fill=(255, 255, 255))
    img.save(str(path), quality=95)


def _create_static_source_video(path: Path, tmp_dir: Path, duration: float = 3.0) -> None:
    """Gera vídeo sintético a partir de imagem ESTÁTICA (sem movimento próprio) para evitar falsos positivos."""
    static_img = tmp_dir / f"static_src_{path.stem}.jpg"
    _create_patterned_image(static_img, (80, 100, 140), (230, 190, 60), "STATIC_SRC")
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(static_img),
        "-t",
        f"{duration:.2f}",
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Falha ao gerar vídeo estático sintético: {res.stderr}"


def _create_silent_wav(path: Path, duration: float = 12.5) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=44100:cl=stereo:d={duration}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Falha ao gerar áudio silencioso: {res.stderr}"


def _create_srt(path: Path, durations: List[float]) -> None:
    lines = []
    t = 0.0
    for idx, d in enumerate(durations, start=1):
        start_s = t
        end_s = t + d
        t = end_s
        lines.append(
            f"{idx}\n00:00:{start_s:05.2f}".replace(".", ",")
            + f" --> 00:00:{end_s:05.2f}".replace(".", ",")
            + f"\nScene {idx} narration\n"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _ffprobe_json(path: Path) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"ffprobe falhou: {res.stderr}"
    return json.loads(res.stdout)


def _extract_frame(video_path: Path, timestamp_sec: float, out_png: Path) -> np.ndarray:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_sec:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(out_png),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Extração de frame falhou: {res.stderr}"
    return np.asarray(Image.open(out_png).convert("RGB"), dtype=np.float32)


# ---------------------------------------------------------------------------
# ETAPA 2 — TESTE INTEGRADO DO MASTER AV PLAN -> ADAPTER -> CLOUD HUB -> INTERPRETER
# ---------------------------------------------------------------------------

def test_etapa2_master_plan_to_cloud_hub_preserves_all_fields():
    plan = _build_master_plan_5_scenes()
    adapter = MasterPlanAdapter()
    adapted = adapter.to_pipeline_b(plan)
    payload_dict = dataclasses.asdict(adapted)

    job_obj = SimpleNamespace(
        id="job_e2e_001",
        topic="The 1999 Radar Anomaly",
        script_data=payload_dict,
        video_director_mode="director",
    )
    summary = integrate_video_director(
        job=job_obj,
        tenant_id="tenant_e2e",
        explicit_mode="director",
        director_factory=lambda: SimpleNamespace(create_master_plan=lambda **kw: plan),
        adapter_factory=lambda: adapter,
    )
    assert summary["director_execution_completed"] is True
    assert summary["adapter_validation_passed"] is True
    script_data = job_obj.script_data
    assert "director_metadata" in script_data
    assert len(script_data["scenes"]) == 5

    for idx, scene in enumerate(script_data["scenes"], start=1):
        assert scene["scene_id"] == idx
        assert scene["narration"].startswith(f"Narration for sc_{idx:03d}")
        assert scene["duration_seconds"] == pytest.approx(2.5)
        assert "b_roll_visual" in scene
        assert "camera_movement" in scene
        assert "visual_production_type" in scene
        assert "visual_cues" in scene
        assert "continuity_anchor" in scene
    assert "visual_bible" in script_data["director_metadata"]

    interpreted = interpret_director_plan_safe(script_data)
    assert interpreted.enabled is True
    assert len(interpreted.scenes) == 5
    assert interpreted.global_style.dominant_style == "cinematic documentary"
    assert [s.camera_movement.movement for s in interpreted.scenes] == [
        "slow_push_in",
        "tracking_lateral",
        "static",
        "dramatic_pull_out",
        "static",
    ]


# ---------------------------------------------------------------------------
# ETAPA 3 — TESTE INTEGRADO DO DESKTOP NODE VIA render_video()
# ---------------------------------------------------------------------------

def test_etapa3_desktop_node_render_video_orchestration(tmp_path: Path):
    import desktop_node

    plan = _build_master_plan_5_scenes()
    script_data = dataclasses.asdict(MasterPlanAdapter().to_pipeline_b(plan))

    durations = [2.5] * 5
    total_dur = sum(durations)
    synth_wav = tmp_path / "synth_voice.wav"
    synth_srt = tmp_path / "synth.srt"
    _create_silent_wav(synth_wav, duration=total_dur)
    _create_srt(synth_srt, durations)

    # Cria 5 assets locais únicos no cache para evitar chamadas externas
    scene_assets: List[Path] = []
    for idx in range(5):
        p = tmp_path / f"asset_{idx+1}.jpg"
        _create_patterned_image(p, (20 + idx * 20, 40, 80), (220, 180, 50), f"S{idx+1}")
        scene_assets.append(p)

    call_count = {"interpret": 0, "assemble": 0}
    orig_interpret = DirectorExecutionInterpreter.interpret
    captured: Dict[str, Any] = {}

    def _counting_interpret(self, sd):
        call_count["interpret"] += 1
        return orig_interpret(self, sd)

    def _spy_assemble(job_id, video_paths, audio_path, srt_path, topic, clip_durations, total_audio_sec, job_data, **kwargs):
        call_count["assemble"] += 1
        captured["video_paths"] = list(video_paths)
        captured["scene_directions"] = list(job_data.get("_scene_directions", []))
        return True

    asset_iter = itertools.cycle(scene_assets)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    job_data = {
        "job_id": "job_etapa3_001",
        "topic": "The 1999 Radar Anomaly",
        "script_data": script_data,
        "is_short": True,
    }

    with patch.object(DirectorExecutionInterpreter, "interpret", _counting_interpret), \
         patch.object(desktop_node, "OUTPUT_DIR", str(out_dir)), \
         patch.object(desktop_node, "ASSETS_CACHE", str(tmp_path)), \
         patch.object(desktop_node, "generate_voice_master", side_effect=lambda txt, out, **kw: shutil.copy(synth_wav, out)), \
         patch.object(desktop_node, "generate_subtitles_locally", return_value=str(synth_srt)), \
         patch.object(desktop_node, "fetch_sfx", return_value=None), \
         patch.object(desktop_node, "fetch_premium_broll", side_effect=lambda q, used=None, **kw: str(next(asset_iter))), \
         patch.object(desktop_node, "generate_pollinations_image", side_effect=lambda p, out: False), \
         patch.object(desktop_node, "assemble_production", side_effect=_spy_assemble):
        desktop_node.render_video(job_data)

    assert call_count["interpret"] == 1
    assert call_count["assemble"] == 1
    assert len(captured["scene_directions"]) == 5
    assert [d.camera_movement.movement for d in captured["scene_directions"]] == [
        "slow_push_in",
        "tracking_lateral",
        "static",
        "dramatic_pull_out",
        "static",
    ]
    assert len(set(captured["video_paths"][:5])) == 5


# ---------------------------------------------------------------------------
# ETAPA 4 — TESTE INTEGRADO DO DIRECTOR CAMERA ENGINE (FFMPEG + VISUAL)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "movement,is_image,expect_motion",
    [
        ("slow_push_in", True, True),
        ("dramatic_pull_out", False, True),
        ("tracking_lateral", True, True),
        ("pan_left", True, True),
        ("tilt_up", True, True),
        ("tilt_down", True, True),
        ("static", True, False),
        ("unknown_camera_move_xyz", True, False),
    ],
)
def test_etapa4_camera_engine_all_movements_real_ffmpeg(
    tmp_path: Path, movement: str, is_image: bool, expect_motion: bool
):
    direction = SimpleNamespace(camera_movement=SimpleNamespace(movement=movement))
    flt = resolve_scene_camera_filter(
        direction, duration_sec=2.0, width=1280, height=720, fps=30, is_image=is_image
    )
    assert flt is not None and len(flt) > 0

    src = tmp_path / ("in.mp4" if not is_image else "in.jpg")
    if is_image:
        _create_patterned_image(src, (30, 60, 90), (240, 200, 60), movement)
        input_args = ["-loop", "1", "-t", "2.0", "-i", str(src)]
    else:
        _create_static_source_video(src, tmp_path, duration=2.5)
        input_args = ["-i", str(src)]

    out_mp4 = tmp_path / f"out_{movement}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-vf",
        flt,
        "-t",
        "2.0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg falhou para {movement}: {res.stderr}"
    assert out_mp4.exists() and out_mp4.stat().st_size > 5000

    info = _ffprobe_json(out_mp4)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert vstream["width"] == 1280
    assert vstream["height"] == 720
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.25)

    f_start = _extract_frame(out_mp4, 0.2, tmp_path / f"{movement}_0.png")
    f_mid = _extract_frame(out_mp4, 1.0, tmp_path / f"{movement}_1.png")
    f_end = _extract_frame(out_mp4, 1.8, tmp_path / f"{movement}_2.png")
    _assert_no_black_borders(Image.fromarray(f_start.astype(np.uint8)))
    _assert_no_black_borders(Image.fromarray(f_mid.astype(np.uint8)))
    _assert_no_black_borders(Image.fromarray(f_end.astype(np.uint8)))

    def _measure_asymmetric_marker(arr: np.ndarray) -> tuple[int, float, float]:
        # Isola exclusivamente o marcador verde assimétrico (20, 240, 20),
        # evitando grade, texto e bordas brancas do retângulo.
        mask = (arr[:, :, 1] > 180) & (arr[:, :, 0] < 100) & (arr[:, :, 2] < 100)
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return 0, 0.0, 0.0
        return int(len(xs)), float(xs.mean()), float(ys.mean())

    a0, cx0, cy0 = _measure_asymmetric_marker(f_start)
    a_mid, cx_mid, cy_mid = _measure_asymmetric_marker(f_mid)
    a1, cx1, cy1 = _measure_asymmetric_marker(f_end)
    assert a0 > 100 and a_mid > 100 and a1 > 100, (
        f"Marcador assimétrico não isolado em {movement}: a0={a0}, a_mid={a_mid}, a1={a1}"
    )

    diff = float(np.mean(np.abs(f_start - f_end)))
    if expect_motion:
        assert diff > 0.5, f"Movimento {movement} não produziu deslocamento visual (diff={diff})"
        if movement == "slow_push_in":
            assert a_mid > a0 * 1.03 and a1 > a_mid * 1.03 and a1 > a0 * 1.08, (
                f"slow_push_in deveria ampliar área progressivamente ({a0} -> {a_mid} -> {a1})"
            )
        elif movement == "dramatic_pull_out":
            assert a_mid < a0 * 0.97 and a1 < a_mid * 0.97 and a1 < a0 * 0.92, (
                f"dramatic_pull_out deveria reduzir área progressivamente ({a0} -> {a_mid} -> {a1})"
            )
        elif movement == "tracking_lateral":
            # Janela de crop avança para a direita -> elemento estacionário desloca para a esquerda (cx diminui)
            assert cx_mid < cx0 - 2.0 and cx1 < cx_mid - 2.0 and cx1 < cx0 - 5.0, (
                f"tracking_lateral deveria deslocar elemento para a esquerda ({cx0:.2f} -> {cx_mid:.2f} -> {cx1:.2f})"
            )
        elif movement == "pan_left":
            # Janela de crop recua para a esquerda -> elemento estacionário desloca para a direita (cx aumenta)
            assert cx_mid > cx0 + 2.0 and cx1 > cx_mid + 2.0 and cx1 > cx0 + 5.0, (
                f"pan_left deveria deslocar elemento para a direita ({cx0:.2f} -> {cx_mid:.2f} -> {cx1:.2f})"
            )
        elif movement == "tilt_up":
            # Janela de crop sobe -> elemento estacionário desloca para baixo (cy aumenta)
            assert cy_mid > cy0 + 2.0 and cy1 > cy_mid + 2.0 and cy1 > cy0 + 5.0, (
                f"tilt_up deveria deslocar elemento para baixo ({cy0:.2f} -> {cy_mid:.2f} -> {cy1:.2f})"
            )
        elif movement == "tilt_down":
            # Janela de crop desce -> elemento estacionário desloca para cima (cy diminui)
            assert cy_mid < cy0 - 2.0 and cy1 < cy_mid - 2.0 and cy1 < cy0 - 5.0, (
                f"tilt_down deveria deslocar elemento para cima ({cy0:.2f} -> {cy_mid:.2f} -> {cy1:.2f})"
            )
    else:
        assert diff < 1.0, f"Movimento {movement} produziu movimento artificial (diff={diff})"
        assert abs(cx1 - cx0) <= 1.0 and abs(cy1 - cy0) <= 1.0, (
            f"{movement} deveria manter centroide estático ({cx0:.2f},{cy0:.2f} -> {cx1:.2f},{cy1:.2f})"
        )
        assert abs(a1 - a0) <= max(50, a0 * 0.02), (
            f"{movement} deveria manter área estática ({a0} -> {a1})"
        )


# ---------------------------------------------------------------------------
# ETAPA 5 — TESTE END-TO-END VIA render_video() COM MATERIAL SINTÉTICO
# ---------------------------------------------------------------------------

def test_etapa5_e2e_synthetic_5_scenes_render_video(tmp_path: Path):
    import desktop_node

    plan = _build_master_plan_5_scenes()
    script_data = dataclasses.asdict(MasterPlanAdapter().to_pipeline_b(plan))

    durations = [2.5] * 5
    total_dur = sum(durations)
    synth_wav = tmp_path / "e2e_voice.wav"
    synth_srt = tmp_path / "e2e_subs.srt"
    _create_silent_wav(synth_wav, duration=total_dur)
    _create_srt(synth_srt, durations)

    media_paths: List[Path] = []
    for idx in range(5):
        if idx == 3:  # Cena 4: vídeo estático para dramatic_pull_out
            vp = tmp_path / f"scene_{idx+1}.mp4"
            _create_static_source_video(vp, tmp_path, duration=3.0)
            media_paths.append(vp)
        else:
            ip = tmp_path / f"scene_{idx+1}.jpg"
            _create_patterned_image(
                ip,
                bg_rgb=(60 + idx * 25, 80, 120),
                accent_rgb=(220, 180 - idx * 20, 50),
                label=f"SCENE {idx+1}",
            )
            media_paths.append(ip)

    thumb_img = tmp_path / "thumb.jpg"
    _create_patterned_image(thumb_img, (10, 20, 30), (200, 150, 50), "THUMB")

    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    cpu_enc = {
        "encoder": "libx264",
        "preset": "ultrafast",
        "pix_fmt": "yuv420p",
        "args": [],
        "hw_accel": False,
    }

    asset_iter = itertools.cycle(media_paths)
    job_data: Dict[str, Any] = {
        "job_id": "e2e_synth_001",
        "topic": "The 1999 Radar Anomaly",
        "script_data": script_data,
        "is_short": True,
    }

    with patch.object(desktop_node, "OUTPUT_DIR", str(out_dir)), \
         patch.object(desktop_node, "ASSETS_CACHE", str(tmp_path)), \
         patch.object(desktop_node, "detect_best_encoder", return_value=cpu_enc), \
         patch.object(desktop_node, "generate_voice_master", side_effect=lambda txt, out, **kw: shutil.copy(synth_wav, out)), \
         patch.object(desktop_node, "generate_subtitles_locally", return_value=str(synth_srt)), \
         patch.object(desktop_node, "fetch_sfx", return_value=None), \
         patch.object(desktop_node, "fetch_premium_broll", side_effect=lambda q, used=None, **kw: str(next(asset_iter))), \
         patch.object(desktop_node, "generate_pollinations_image", side_effect=lambda p, out: False):
        desktop_node.render_video(job_data)

    final_videos = list(out_dir.rglob("*.mp4"))
    assert len(final_videos) >= 1
    final_mp4 = final_videos[0]
    assert final_mp4.stat().st_size > 20_000

    info = _ffprobe_json(final_mp4)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert vstream["codec_name"] == "h264"
    assert vstream["width"] == 1280
    assert vstream["height"] == 720
    assert float(info["format"]["duration"]) == pytest.approx(total_dur, abs=2.5)

    # Cenas 0, 1, 3 são dinâmicas (slow_push_in, tracking_lateral, dramatic_pull_out)
    # Cenas 2, 4 são estáticas (CRANE_SWEEP->static, static)
    expected_dynamic = {0: True, 1: True, 2: False, 3: True, 4: False}
    for scene_idx in range(5):
        t_start = scene_idx * 2.5 + 0.8
        t_end = scene_idx * 2.5 + 1.5
        f1 = _extract_frame(final_mp4, t_start, tmp_path / f"f_{scene_idx}_a.png")
        f2 = _extract_frame(final_mp4, t_end, tmp_path / f"f_{scene_idx}_b.png")
        assert f1.mean() > 5.0 and f2.mean() > 5.0, f"Frame preto na cena {scene_idx+1}"
        _assert_no_black_borders(Image.fromarray(f1.astype(np.uint8)))
        _assert_no_black_borders(Image.fromarray(f2.astype(np.uint8)))
        diff = float(np.mean(np.abs(f1 - f2)))
        if expected_dynamic[scene_idx]:
            assert diff > 0.5, f"Cena {scene_idx+1} sem movimento perceptível (diff={diff})"


# ---------------------------------------------------------------------------
# ETAPA 6 — REGRESSÃO LEGACY VIA render_video() (SEM director_metadata)
# ---------------------------------------------------------------------------

def test_etapa6_legacy_flow_preserved_via_render_video(tmp_path: Path):
    import desktop_node

    legacy_script = {
        "title": "Legacy Documentary",
        "scenes": [
            {"narration": "Scene one.", "b_roll_visual": "city skyline", "duration": 2.0},
            {"narration": "Scene two.", "b_roll_visual": "ocean waves", "duration": 2.0},
        ],
    }
    plan = interpret_director_plan_safe(legacy_script)
    assert plan.enabled is False

    img1 = tmp_path / "leg1.jpg"
    img2 = tmp_path / "leg2.jpg"
    _create_patterned_image(img1, (30, 30, 60), (200, 200, 200), "LEG1")
    _create_patterned_image(img2, (60, 30, 30), (200, 200, 200), "LEG2")

    audio_path = tmp_path / "leg.wav"
    srt_path = tmp_path / "leg.srt"
    _create_silent_wav(audio_path, duration=4.0)
    _create_srt(srt_path, [2.0, 2.0])

    out_dir = tmp_path / "legacy_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cpu_enc = {
        "encoder": "libx264",
        "preset": "ultrafast",
        "pix_fmt": "yuv420p",
        "args": [],
        "hw_accel": False,
    }
    asset_iter = itertools.cycle([img1, img2])
    job_data = {
        "job_id": "legacy_001",
        "topic": "Legacy Documentary",
        "script_data": legacy_script,
        "is_short": True,
    }

    with patch("desktop_worker.director_camera_engine.resolve_scene_camera_filter", wraps=resolve_scene_camera_filter) as spy_cam, \
         patch.object(desktop_node, "OUTPUT_DIR", str(out_dir)), \
         patch.object(desktop_node, "ASSETS_CACHE", str(tmp_path)), \
         patch.object(desktop_node, "detect_best_encoder", return_value=cpu_enc), \
         patch.object(desktop_node, "generate_voice_master", side_effect=lambda txt, out, **kw: shutil.copy(audio_path, out)), \
         patch.object(desktop_node, "generate_subtitles_locally", return_value=str(srt_path)), \
         patch.object(desktop_node, "fetch_sfx", return_value=None), \
         patch.object(desktop_node, "fetch_premium_broll", side_effect=lambda q, used=None, **kw: str(next(asset_iter))), \
         patch.object(desktop_node, "generate_pollinations_image", side_effect=lambda p, out: False):
        desktop_node.render_video(job_data)

    assert spy_cam.call_count == 0
    assert "_scene_directions" not in job_data
    assert len(list(out_dir.rglob("*.mp4"))) >= 1


# ---------------------------------------------------------------------------
# ETAPA 7 — ISOLAMENTO, CONCORRÊNCIA E OBSERVABILIDADE
# ---------------------------------------------------------------------------

def test_etapa7_concurrent_jobs_isolation_and_observability(tmp_path: Path):
    plan_a = _build_master_plan_5_scenes(plan_id="plan_tenant_A")
    plan_b = _build_master_plan_5_scenes(plan_id="plan_tenant_B")
    plan_b.scenes[0].visual_direction.camera_movement = CameraMovement.TRACKING_LATERAL

    sd_a = dataclasses.asdict(MasterPlanAdapter().to_pipeline_b(plan_a))
    sd_b = dataclasses.asdict(MasterPlanAdapter().to_pipeline_b(plan_b))

    asset_a = tmp_path / "tenant_a_asset.jpg"
    asset_b = tmp_path / "tenant_b_asset.jpg"
    _create_patterned_image(asset_a, (40, 80, 120), (220, 180, 60), "TENANT_A")
    _create_patterned_image(asset_b, (120, 80, 40), (60, 180, 220), "TENANT_B")

    collector = VideoDirectorObservabilityCollector()

    def _run_job(
        sd: Dict[str, Any],
        tenant_id: str,
        job_id: str,
        corr_id: str,
        exec_id: str,
        asset_path: Path,
    ) -> Dict[str, Any]:
        interpreted = interpret_director_plan_safe(sd)
        by_id, by_order = build_scene_direction_index(interpreted)
        d0 = resolve_scene_direction(interpreted, by_id, by_order, sd["scenes"][0], 0)
        assert d0 is not None
        used: set = set()
        selected_path, _ = select_media_with_direction(
            visual_kw=sd["scenes"][0].get("b_roll_query", "radar"),
            scene_text=sd["scenes"][0]["narration"],
            scene_index=0,
            scene_direction=d0,
            used_assets=used,
            assets_cache_dir=str(tmp_path),
            fetch_broll_fn=lambda q, u: str(asset_path),
            generate_pollinations_fn=lambda p, out: False,
            find_local_cache_fn=lambda q, u: None,
        )
        used.add(selected_path)
        collector.emit_event(
            event_name="director.completed",
            job_id=job_id,
            tenant_id=tenant_id,
            correlation_id=corr_id,
            execution_id=exec_id,
            stage="director_engine",
            video_director_mode="director",
            status="completed",
        )
        return {
            "movement": d0.camera_movement.movement,
            "scene_id": d0.scene_id,
            "selected_path": selected_path,
            "used": used,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_run_job, sd_a, "tenant_a", "job_a", "corr_a", "exec_a", asset_a)
        fut_b = pool.submit(_run_job, sd_b, "tenant_b", "job_b", "corr_b", "exec_b", asset_b)
        res_a = fut_a.result()
        res_b = fut_b.result()

    assert res_a["movement"] == "slow_push_in"
    assert res_b["movement"] == "tracking_lateral"
    assert res_a["scene_id"] == sd_a["scenes"][0]["scene_id"]
    assert res_b["scene_id"] == sd_b["scenes"][0]["scene_id"]
    assert res_a["selected_path"] == str(asset_a)
    assert res_b["selected_path"] == str(asset_b)
    assert res_a["used"].isdisjoint(res_b["used"])

    events_a = collector.get_events(tenant_id="tenant_a")
    events_b = collector.get_events(tenant_id="tenant_b")
    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_a[0]["tenant_id"] == "tenant_a"
    assert events_a[0]["job_id"] == "job_a"
    assert events_a[0]["correlation_id"] == "corr_a"
    assert events_a[0]["execution_id"] == "exec_a"
    assert events_b[0]["tenant_id"] == "tenant_b"
    assert events_b[0]["job_id"] == "job_b"
    assert events_b[0]["correlation_id"] == "corr_b"
    assert events_b[0]["execution_id"] == "exec_b"
    assert all(
        e["tenant_id"] == "tenant_a" and e["job_id"] != "job_b" and e["execution_id"] != "exec_b"
        for e in events_a
    )
    assert all(
        e["tenant_id"] == "tenant_b" and e["job_id"] != "job_a" and e["execution_id"] != "exec_a"
        for e in events_b
    )
