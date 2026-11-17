"""
DirectorCameraEngine — Sprint 2.6 (Tarefa 3)

Camada isolada, determinística e stateless para traduzir instruções de movimento
de câmera (CameraMovementInstruction / NormalizedSceneDirection) em filtros FFmpeg
compatíveis com imagens estáticas e vídeos, preservando duração, FPS, resolução
e limites de enquadramento sem bordas pretas.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _clamp_params(duration_sec: float, width: int, height: int, fps: int) -> tuple[float, int, int, int]:
    """Garante parâmetros numéricos válidos para geração de filtros FFmpeg."""
    dur = max(0.1, float(duration_sec or 0.1))
    w = max(64, int(width or 1280))
    h = max(64, int(height or 720))
    f = max(1, int(fps or 30))
    return dur, w, h, f


def _extract_movement_name(camera_movement: Any) -> str:
    """Extrai o nome normalizado do movimento a partir de CameraMovementInstruction, dict ou str."""
    if camera_movement is None:
        return "static"
    if isinstance(camera_movement, str):
        return camera_movement.strip().lower() or "static"
    if isinstance(camera_movement, dict):
        return str(camera_movement.get("movement") or "static").strip().lower()
    return str(getattr(camera_movement, "movement", "static") or "static").strip().lower()


def _normalize_alias(movement: str) -> str:
    """Mapeia aliases comuns para os movimentos canônicos sem alterar o contrato."""
    aliases = {
        "slow_zoom_in": "slow_push_in",
        "zoom_in": "slow_push_in",
        "slow_zoom_out": "dramatic_pull_out",
        "zoom_out": "dramatic_pull_out",
        "pan_right": "tracking_lateral",
        "pan_left": "pan_left",
        "tilt_up": "tilt_up",
        "tilt_down": "tilt_down",
        "static_shot": "static",
    }
    return aliases.get(movement, movement)


def build_camera_filter(
    camera_movement: Any,
    duration_sec: float,
    width: int,
    height: int,
    fps: int = 30,
    is_image: bool = True,
) -> str:
    """
    Constrói o filtro FFmpeg determinístico para o movimento de câmera informado.

    - Para imagens (`is_image=True`): utiliza `zoompan` com coordenadas limitadas
      para evitar bordas pretas, seguido de `scale`/`crop` para a resolução exata.
    - Para vídeos (`is_image=False`): evita `zoompan` (que congela vídeo) e usa
      `scale` com overscan + `crop` dinâmico/estático seguro.
    """
    dur, w, h, f = _clamp_params(duration_sec, width, height, fps)
    num_frames = max(1, int(round(dur * f)) + 1)
    raw_movement = _extract_movement_name(camera_movement)
    movement = _normalize_alias(raw_movement)

    if not is_image:
        return _build_video_filter(movement, dur, w, h, f)

    return _build_image_filter(movement, dur, num_frames, w, h, f)


def _build_image_filter(
    movement: str,
    dur: float,
    num_frames: int,
    w: int,
    h: int,
    fps: int,
) -> str:
    """Constrói filtro FFmpeg para imagens estáticas usando zoompan seguro."""
    tail = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={fps},trim=duration={dur:.3f}"
    )

    if movement == "slow_push_in":
        zp = (
            f"zoompan=z='min(zoom+0.003,1.25)':d={num_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement == "dramatic_pull_out":
        zp = (
            f"zoompan=z='if(eq(on,1),1.30,max(zoom-0.003,1.0))':d={num_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement == "tracking_lateral":
        zp = (
            f"zoompan=z='1.15':d={num_frames}"
            f":x='min(iw-iw/zoom,max(0,(iw-iw/zoom)*(on/{num_frames})))'"
            f":y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement == "pan_left":
        zp = (
            f"zoompan=z='1.15':d={num_frames}"
            f":x='max(0,min(iw-iw/zoom,(iw-iw/zoom)*(1-on/{num_frames})))'"
            f":y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement in ("dutch_tilt", "tilt_up"):
        zp = (
            f"zoompan=z='1.12':d={num_frames}"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='max(0,min(ih-ih/zoom,(ih-ih/zoom)*(1-on/{num_frames})))':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement == "tilt_down":
        zp = (
            f"zoompan=z='1.12':d={num_frames}"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='min(ih-ih/zoom,max(0,(ih-ih/zoom)*(on/{num_frames})))':s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    if movement == "impact_micro_shake":
        zp = (
            f"zoompan=z='1.10':d={num_frames}"
            f":x='max(0,min(iw-iw/zoom,iw/2-(iw/zoom/2)+if(lt(on,12),sin(on*2.5)*6,0)))'"
            f":y='max(0,min(ih-ih/zoom,ih/2-(ih/zoom/2)+if(lt(on,12),cos(on*2.5)*6,0)))'"
            f":s={w}x{h}:fps={fps}"
        )
        return f"scale=4000:-1,{zp},{tail}"

    # static ou movimento desconhecido -> sem movimento artificial
    return tail


def _build_video_filter(
    movement: str,
    dur: float,
    w: int,
    h: int,
    fps: int,
) -> str:
    """Constrói filtro FFmpeg seguro para vídeos (sem zoompan)."""
    tail = f"setsar=1,fps={fps},trim=duration={dur:.3f}"

    if movement == "slow_push_in":
        prog = f"min(1,max(0,t/{dur:.3f}))"
        return (
            f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"scale=w='iw*(1+0.25*{prog})':h='ih*(1+0.25*{prog})':eval=frame,"
            f"crop={w}:{h}:x='(iw-ow)/2':y='(ih-oh)/2',{tail}"
        )

    if movement == "dramatic_pull_out":
        prog = f"min(1,max(0,t/{dur:.3f}))"
        return (
            f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"scale=w='iw*(1.25-0.25*{prog})':h='ih*(1.25-0.25*{prog})':eval=frame,"
            f"crop={w}:{h}:x='(iw-ow)/2':y='(ih-oh)/2',{tail}"
        )

    if movement == "tracking_lateral":
        ow, oh = int(w * 1.15), int(h * 1.15)
        return (
            f"scale=w={ow}:h={oh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*min(1,max(0,t/{dur:.3f}))':y='(ih-oh)/2',{tail}"
        )

    if movement == "pan_left":
        ow, oh = int(w * 1.15), int(h * 1.15)
        return (
            f"scale=w={ow}:h={oh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*(1-min(1,max(0,t/{dur:.3f})))':y='(ih-oh)/2',{tail}"
        )

    if movement in ("dutch_tilt", "tilt_up"):
        ow, oh = int(w * 1.12), int(h * 1.12)
        return (
            f"scale=w={ow}:h={oh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)/2':y='(ih-oh)*(1-min(1,max(0,t/{dur:.3f})))',{tail}"
        )

    if movement == "tilt_down":
        ow, oh = int(w * 1.12), int(h * 1.12)
        return (
            f"scale=w={ow}:h={oh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)/2':y='(ih-oh)*min(1,max(0,t/{dur:.3f}))',{tail}"
        )

    if movement == "impact_micro_shake":
        ow, oh = int(w * 1.10), int(h * 1.10)
        return (
            f"scale=w={ow}:h={oh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='max(0,min(iw-ow,(iw-ow)/2+if(lt(t,0.4),sin(t*75)*4,0)))'"
            f":y='max(0,min(ih-oh,(ih-oh)/2+if(lt(t,0.4),cos(t*75)*4,0)))',{tail}"
        )

    # static ou desconhecido
    return f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,crop={w}:{h},{tail}"


def resolve_scene_camera_filter(
    scene_direction: Any,
    duration_sec: float,
    width: int,
    height: int,
    fps: int = 30,
    is_image: bool = True,
) -> Optional[str]:
    """
    Resolve o filtro FFmpeg de câmera para uma cena dirigida.
    Retorna None quando `scene_direction` é None (modo Legacy).
    """
    if scene_direction is None:
        return None
    try:
        movement = getattr(scene_direction, "camera_movement", None)
        return build_camera_filter(
            camera_movement=movement,
            duration_sec=duration_sec,
            width=width,
            height=height,
            fps=fps,
            is_image=is_image,
        )
    except Exception as exc:
        logger.warning("DirectorCameraEngine fallback para static devido a erro: %s", exc)
        return build_camera_filter(
            camera_movement="static",
            duration_sec=duration_sec,
            width=width,
            height=height,
            fps=fps,
            is_image=is_image,
        )
