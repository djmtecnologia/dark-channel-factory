"""
Testes unitários e de integração para DirectorCameraEngine (Sprint 2.6 — Tarefa 3).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from desktop_worker.director_camera_engine import (
    build_camera_filter,
    resolve_scene_camera_filter,
)
from desktop_worker.director_execution_interpreter import (
    CameraMovementMapper,
)
from desktop_worker.director_media_router import (
    build_scene_direction_index,
    interpret_director_plan_safe,
    resolve_scene_direction,
)


# =============================================================================
# A. Movimentos de Câmera
# =============================================================================

@pytest.mark.parametrize(
    "movement,expected_token",
    [
        ("slow_push_in", "min(zoom+0.003,1.25)"),
        ("slow_zoom_in", "min(zoom+0.003,1.25)"),
        ("dramatic_pull_out", "if(eq(on,1),1.30,max(zoom-0.003,1.0))"),
        ("slow_zoom_out", "if(eq(on,1),1.30,max(zoom-0.003,1.0))"),
        ("tracking_lateral", "z='1.15'"),
        ("pan_right", "z='1.15'"),
        ("pan_left", "1-on/"),
        ("dutch_tilt", "z='1.12'"),
        ("tilt_up", "z='1.12'"),
        ("tilt_down", "ih-ih/zoom"),
        ("impact_micro_shake", "sin(on*2.5)*6"),
    ],
)
def test_camera_movements_image_filters(movement: str, expected_token: str):
    flt = build_camera_filter(movement, duration_sec=4.0, width=1920, height=1080, fps=30, is_image=True)
    assert "zoompan=" in flt
    assert expected_token in flt
    assert "scale=1920:1080" in flt
    assert "crop=1920:1080" in flt
    assert "trim=duration=4.000" in flt


def test_static_and_unknown_movements_have_no_zoompan():
    for mv in ("static", "static_shot", "unknown_crazy_move", None):
        flt = build_camera_filter(mv, duration_sec=3.0, width=1280, height=720, fps=30, is_image=True)
        assert "zoompan=" not in flt
        assert "scale=1280:720" in flt
        assert "crop=1280:720" in flt
        assert "trim=duration=3.000" in flt


# =============================================================================
# B. Parâmetros de Renderização (Duração, FPS, Resoluções, Imagem vs Vídeo)
# =============================================================================

@pytest.mark.parametrize("w,h", [(1920, 1080), (1080, 1920), (1280, 720), (720, 1280)])
@pytest.mark.parametrize("fps", [24, 30, 60])
@pytest.mark.parametrize("dur", [1.5, 5.0, 12.25])
def test_rendering_params_preserved(w: int, h: int, fps: int, dur: float):
    flt = build_camera_filter("slow_push_in", duration_sec=dur, width=w, height=h, fps=fps, is_image=True)
    expected_frames = int(round(dur * fps)) + 1
    assert f":d={expected_frames}:" in flt
    assert f":s={w}x{h}:fps={fps}" in flt
    assert f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}" in flt
    assert f"fps={fps},trim=duration={dur:.3f}" in flt


@pytest.mark.parametrize(
    "movement",
    ["slow_push_in", "dramatic_pull_out", "tracking_lateral", "pan_left", "dutch_tilt", "tilt_down", "impact_micro_shake", "static"],
)
def test_video_source_never_uses_zoompan(movement: str):
    flt = build_camera_filter(movement, duration_sec=4.0, width=1920, height=1080, fps=30, is_image=False)
    assert "zoompan=" not in flt
    assert "crop=1920:1080" in flt
    assert "trim=duration=4.000" in flt


# =============================================================================
# C. Segurança Visual
# =============================================================================

def test_visual_safety_clamps_invalid_inputs():
    flt = build_camera_filter("slow_push_in", duration_sec=-5.0, width=0, height=-100, fps=0, is_image=True)
    assert "d=" in flt
    assert "trim=duration=0.100" in flt
    assert "scale=1280:64" in flt


def test_resolve_scene_camera_filter_legacy_returns_none():
    assert resolve_scene_camera_filter(None, 5.0, 1280, 720) is None


def test_resolve_scene_camera_filter_with_mapper_instruction():
    mapper = CameraMovementMapper()
    instr = mapper.map_movement("tracking_lateral")

    class DummyDir:
        camera_movement = instr

    flt = resolve_scene_camera_filter(DummyDir(), 6.0, 1920, 1080, fps=30, is_image=True)
    assert flt is not None
    assert "zoompan=z='1.15'" in flt


# =============================================================================
# D. Integração Real com Desktop Node (assemble_production) + FFmpeg Sintético
# =============================================================================

def test_desktop_node_assemble_production_applies_director_camera_and_preserves_legacy(tmp_path):
    import desktop_node

    script_data = {
        "director_metadata": {"enabled": True, "plan_id": "plan-cam-1", "schema_version": "2.0"},
        "scenes": [
            {"scene_id": 1, "order": 1, "camera_movement": "slow_push_in"},
            {"scene_id": 2, "order": 2, "camera_movement": "dramatic_pull_out"},
        ],
    }
    plan = interpret_director_plan_safe(script_data)
    assert plan.enabled is True
    by_id, by_order = build_scene_direction_index(plan)
    directions = [
        resolve_scene_direction(plan, by_id, by_order, s, idx)
        for idx, s in enumerate(script_data["scenes"])
    ]

    img1 = str(tmp_path / "s1.png")
    img2 = str(tmp_path / "s2.png")
    audio = str(tmp_path / "narration.mp3")
    srt = str(tmp_path / "subs.srt")
    for p in (img1, img2, audio):
        with open(p, "wb") as f:
            f.write(b"dummy")
    with open(srt, "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nHello\n")

    captured_filters = {}

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "-filter_complex_script" in cmd:
            idx = cmd.index("-filter_complex_script")
            script_path = cmd[idx + 1]
            if os.path.exists(script_path):
                with open(script_path, "r", encoding="utf-8") as f:
                    captured_filters[os.path.basename(script_path)] = f.read()
        class Res:
            returncode = 0
            stderr = ""
            stdout = ""
        return Res()

    with patch.object(desktop_node, "OUTPUT_DIR", str(tmp_path)), \
         patch("subprocess.run", side_effect=fake_run):
        job_data_dir = {"_scene_directions": directions, "_scene_visual_cues": [[], []]}
        desktop_node.assemble_production(
            "job_dir_test", [img1, img2], audio, srt, "Topic", [3.0, 4.0], 7.0,
            job_data=job_data_dir, is_short=False
        )

        fc = captured_filters.get("filter_job_dir_test.txt", "")
        assert fc, "filter_job_dir_test.txt não foi capturado na execução do FFmpeg"
        assert "min(zoom+0.003,1.25)" in fc
        assert "if(eq(on,1),1.30,max(zoom-0.003,1.0))" in fc
        assert "trim=duration=3.000" in fc
        assert "trim=duration=4.000" in fc

        # Legacy: sem _scene_directions -> não usa filtros do Camera Engine
        job_data_leg = {"_scene_visual_cues": [[], []]}
        desktop_node.assemble_production(
            "job_leg_test", [img1, img2], audio, srt, "Topic", [3.0, 4.0], 7.0,
            job_data=job_data_leg, is_short=False
        )
        fc_leg = captured_filters.get("filter_job_leg_test.txt", "")
        assert fc_leg, "filter_job_leg_test.txt não foi capturado na execução do FFmpeg"
        assert "if(eq(on,1),1.30,max(zoom-0.003,1.0))" not in fc_leg


def test_ffmpeg_synthetic_render_with_camera_filter(tmp_path):
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        pytest.skip("FFmpeg não disponível no PATH")

    out_mp4 = tmp_path / "synthetic_cam.mp4"
    flt = build_camera_filter("slow_push_in", duration_sec=1.0, width=320, height=180, fps=15, is_image=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=15",
        "-vf", flt,
        "-t", "1.0",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"FFmpeg falhou: {res.stderr}"
    assert out_mp4.exists() and out_mp4.stat().st_size > 0


def _extract_frame_rgb(ffmpeg_bin: str, video_path: str, timestamp_sec: float, out_png: str):
    """Extrai um frame RGB em timestamp_sec usando FFmpeg e retorna os bytes/imagem PIL."""
    from PIL import Image

    cmd = [
        ffmpeg_bin, "-y",
        "-ss", f"{timestamp_sec:.3f}",
        "-i", video_path,
        "-frames:v", "1",
        out_png,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, f"Falha ao extrair frame: {res.stderr}"
    return Image.open(out_png).convert("RGB")


def _assert_no_black_borders(img) -> None:
    """Verifica todos os pixels do perímetro do frame, exigindo >= 99.5% não pretos."""
    w, h = img.size
    pixels = img.load()
    perimeter = []
    for x in range(w):
        perimeter.append(pixels[x, 0])
        if h > 1:
            perimeter.append(pixels[x, h - 1])
    for y in range(1, h - 1):
        perimeter.append(pixels[0, y])
        if w > 1:
            perimeter.append(pixels[w - 1, y])

    non_black = sum(1 for r, g, b in perimeter if (r + g + b) > 24)
    pct = (non_black / len(perimeter)) * 100.0 if perimeter else 0.0
    assert pct >= 99.5, f"Borda preta detectada no perímetro: {pct:.2f}% de pixels não pretos (mínimo 99.50%)"


def test_assert_no_black_borders_helper():
    from PIL import Image, ImageDraw

    clean = Image.new("RGB", (100, 80), (45, 70, 110))
    _assert_no_black_borders(clean)

    for box in ([0, 0, 5, 79], [94, 0, 99, 79], [0, 0, 99, 5], [0, 74, 99, 79]):
        bad = Image.new("RGB", (100, 80), (45, 70, 110))
        ImageDraw.Draw(bad).rectangle(box, fill=(0, 0, 0))
        with pytest.raises(AssertionError, match="99.50%"):
            _assert_no_black_borders(bad)


def _create_static_asymmetric_video(ffmpeg_bin: str, tmp_path, duration: float = 2.0, fps: int = 15) -> str:
    """
    Cria uma imagem estática assimétrica com elementos identificáveis e a repete
    como vídeo MP4 via FFmpeg, garantindo que qualquer evolução entre frames
    venha exclusivamente do filtro de câmera.
    """
    from PIL import Image, ImageDraw

    img_path = str(tmp_path / "static_asymmetric.png")
    img = Image.new("RGB", (640, 360), (45, 70, 110))
    draw = ImageDraw.Draw(img)
    # Alvo vermelho assimétrico próximo ao centro-superior-esquerdo
    draw.rectangle([220, 110, 340, 210], fill=(240, 30, 30))
    # Elemento verde à direita
    draw.rectangle([450, 140, 560, 230], fill=(30, 220, 60))
    # Elemento amarelo inferior
    draw.rectangle([120, 250, 260, 320], fill=(240, 220, 30))
    img.save(img_path)

    src_mp4 = str(tmp_path / "src_static.mp4")
    gen_cmd = [
        ffmpeg_bin, "-y",
        "-loop", "1",
        "-i", img_path,
        "-t", f"{duration:.2f}",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        src_mp4,
    ]
    res_gen = subprocess.run(gen_cmd, capture_output=True, text=True, timeout=30)
    assert res_gen.returncode == 0, f"Falha ao gerar vídeo estático: {res_gen.stderr}"
    return src_mp4


def _measure_red_target(img) -> tuple[float, float, int, int]:
    """Retorna (cx, cy, width, height) do alvo vermelho no frame."""
    w, h = img.size
    xs, ys = [], []
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if r > 170 and g < 90 and b < 90:
                xs.append(x)
                ys.append(y)
    assert xs and ys, "Alvo vermelho não encontrado no frame"
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    bw = max(xs) - min(xs) + 1
    bh = max(ys) - min(ys) + 1
    return cx, cy, bw, bh


def _get_video_duration(ffmpeg_bin: str, video_path: str) -> float:
    import re
    res = subprocess.run([ffmpeg_bin, "-i", video_path], capture_output=True, text=True, timeout=15)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
    assert m, f"Duração não encontrada na saída do FFmpeg: {res.stderr}"
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


@pytest.mark.parametrize(
    "movement",
    ["slow_push_in", "dramatic_pull_out", "tracking_lateral", "pan_left", "tilt_up", "tilt_down", "static"],
)
def test_ffmpeg_video_camera_movement_from_static_source(tmp_path, movement: str):
    """
    Teste visual rigoroso com vídeo derivado de imagem estática assimétrica:
    - Comprova zoom progressivo real (slow_push_in cresce, dramatic_pull_out encolhe).
    - Comprova deslocamento horizontal real (tracking_lateral vs pan_left).
    - Comprova deslocamento vertical real (tilt_up vs tilt_down).
    - Teste negativo (static): comprova que o enquadramento permanece estável.
    - Verifica duração, resolução e ausência de bordas pretas.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        pytest.skip("FFmpeg não disponível no PATH")

    src_mp4 = _create_static_asymmetric_video(ffmpeg_bin, tmp_path, duration=2.0, fps=15)
    out_mp4 = str(tmp_path / f"out_{movement}.mp4")
    flt = build_camera_filter(movement, duration_sec=2.0, width=320, height=180, fps=15, is_image=False)

    render_cmd = [
        ffmpeg_bin, "-y",
        "-i", src_mp4,
        "-vf", flt,
        "-t", "2.0",
        "-pix_fmt", "yuv420p",
        out_mp4,
    ]
    res_render = subprocess.run(render_cmd, capture_output=True, text=True, timeout=30)
    assert res_render.returncode == 0, f"FFmpeg falhou para {movement}: {res_render.stderr}"

    dur = _get_video_duration(ffmpeg_bin, out_mp4)
    assert 1.8 <= dur <= 2.2, f"Duração inesperada para {movement}: {dur}s"

    f_start = _extract_frame_rgb(ffmpeg_bin, out_mp4, 0.10, str(tmp_path / f"{movement}_start.png"))
    f_mid = _extract_frame_rgb(ffmpeg_bin, out_mp4, 1.00, str(tmp_path / f"{movement}_mid.png"))
    f_end = _extract_frame_rgb(ffmpeg_bin, out_mp4, 1.80, str(tmp_path / f"{movement}_end.png"))

    for frm in (f_start, f_mid, f_end):
        assert frm.size == (320, 180)
        _assert_no_black_borders(frm)

    cx0, cy0, w0, h0 = _measure_red_target(f_start)
    cx1, cy1, w1, h1 = _measure_red_target(f_mid)
    cx2, cy2, w2, h2 = _measure_red_target(f_end)

    if movement == "slow_push_in":
        assert w2 > w1 > w0 and h2 > h1 > h0, f"Zoom in não evoluiu: ({w0}x{h0}) -> ({w1}x{h1}) -> ({w2}x{h2})"
    elif movement == "dramatic_pull_out":
        assert w0 > w1 > w2 and h0 > h1 > h2, f"Zoom out não evoluiu: ({w0}x{h0}) -> ({w1}x{h1}) -> ({w2}x{h2})"
    elif movement == "tracking_lateral":
        assert cx2 < cx1 < cx0, f"Pan right não deslocou para esquerda no quadro: {cx0:.1f} -> {cx1:.1f} -> {cx2:.1f}"
    elif movement == "pan_left":
        assert cx2 > cx1 > cx0, f"Pan left não deslocou para direita no quadro: {cx0:.1f} -> {cx1:.1f} -> {cx2:.1f}"
    elif movement == "tilt_up":
        assert cy2 > cy1 > cy0, f"Tilt up não deslocou para baixo no quadro: {cy0:.1f} -> {cy1:.1f} -> {cy2:.1f}"
    elif movement == "tilt_down":
        assert cy2 < cy1 < cy0, f"Tilt down não deslocou para cima no quadro: {cy0:.1f} -> {cy1:.1f} -> {cy2:.1f}"
    elif movement == "static":
        assert abs(cx2 - cx0) <= 1.0 and abs(cy2 - cy0) <= 1.0
        assert abs(w2 - w0) <= 1 and abs(h2 - h0) <= 1
