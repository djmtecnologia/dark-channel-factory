"""
DirectorExecutionInterpreter — Sprint 2.6 (Tarefa 1)

Camada interpretadora stateless, determinística e independente do Desktop Node.
Transforma os metadados do MasterAVPlan persistidos no script_data (director_metadata,
master_av_plan_summary e scenes) em instruções normalizadas para consumo futuro.

Não realiza chamadas LLM, chamadas HTTP, geração de imagens ou execução FFmpeg.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Constantes e Tabelas Determinísticas
# =============================================================================

VALID_CAMERA_MOVEMENTS = frozenset({
    "slow_push_in",
    "dramatic_pull_out",
    "tracking_lateral",
    "dutch_tilt",
    "impact_micro_shake",
    "static",
})

CAMERA_MOVEMENT_TABLE: Dict[str, Dict[str, Any]] = {
    "slow_push_in": {
        "movement": "slow_push_in",
        "kind": "zoom_in",
        "zoom_start": 1.0,
        "zoom_end": 1.25,
        "pan_x": "center",
        "pan_y": "center",
        "tilt_deg": 0.0,
        "shake_px": 0,
        "ffmpeg_zoompan_expr": "z='min(zoom+0.003,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    },
    "dramatic_pull_out": {
        "movement": "dramatic_pull_out",
        "kind": "zoom_out",
        "zoom_start": 1.30,
        "zoom_end": 1.0,
        "pan_x": "center",
        "pan_y": "center",
        "tilt_deg": 0.0,
        "shake_px": 0,
        "ffmpeg_zoompan_expr": "z='if(eq(on,1),1.30,max(zoom-0.003,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    },
    "tracking_lateral": {
        "movement": "tracking_lateral",
        "kind": "pan_horizontal",
        "zoom_start": 1.15,
        "zoom_end": 1.15,
        "pan_x": "left_to_right",
        "pan_y": "center",
        "tilt_deg": 0.0,
        "shake_px": 0,
        "ffmpeg_zoompan_expr": "z='1.15':x='min(iw-iw/zoom,on*1.5)':y='ih/2-(ih/zoom/2)'",
    },
    "dutch_tilt": {
        "movement": "dutch_tilt",
        "kind": "tilt",
        "zoom_start": 1.12,
        "zoom_end": 1.12,
        "pan_x": "center",
        "pan_y": "center",
        "tilt_deg": 4.0,
        "shake_px": 0,
        "ffmpeg_zoompan_expr": "z='1.12':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    },
    "impact_micro_shake": {
        "movement": "impact_micro_shake",
        "kind": "shake",
        "zoom_start": 1.10,
        "zoom_end": 1.10,
        "pan_x": "oscillate",
        "pan_y": "oscillate",
        "tilt_deg": 0.0,
        "shake_px": 6,
        "ffmpeg_zoompan_expr": "z='1.10':x='iw/2-(iw/zoom/2)+if(lt(on,12),sin(on*2.5)*6,0)':y='ih/2-(ih/zoom/2)+if(lt(on,12),cos(on*2.5)*6,0)'",
    },
    "static": {
        "movement": "static",
        "kind": "static",
        "zoom_start": 1.0,
        "zoom_end": 1.0,
        "pan_x": "center",
        "pan_y": "center",
        "tilt_deg": 0.0,
        "shake_px": 0,
        "ffmpeg_zoompan_expr": "",
    },
}

VALID_VISUAL_PRODUCTION_TYPES = frozenset({
    "local_asset",
    "stock_footage",
    "cinematic_still",
    "motion_graphic",
    "blender_3d",
    "auto",
})

PRODUCTION_ROUTE_TABLE: Dict[str, Dict[str, Any]] = {
    "local_asset": {"strategy": "local_first", "allow_stock": True, "allow_still_gen": False},
    "stock_footage": {"strategy": "stock_first", "allow_stock": True, "allow_still_gen": True},
    "cinematic_still": {"strategy": "still_first", "allow_stock": True, "allow_still_gen": True},
    "motion_graphic": {"strategy": "motion_graphic_fallback_stock", "allow_stock": True, "allow_still_gen": True},
    "blender_3d": {"strategy": "blender_disabled_fallback_still", "allow_stock": True, "allow_still_gen": True},
    "auto": {"strategy": "auto_cost_hierarchy", "allow_stock": True, "allow_still_gen": True},
}


def _safe_str(value: Any, default: str = "") -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    text = str(value).strip()
    return text if text else default


def _safe_opt_str(value: Any) -> Optional[str]:
    s = _safe_str(value, "")
    return s if s else None


def _safe_int(value: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None and parsed < min_val:
        return min_val
    if max_val is not None and parsed > max_val:
        return max_val
    return parsed


def _freeze_value(value: Any) -> Any:
    """
    Converte recursivamente estruturas mutáveis (dict, list, set) em equivalentes
    profundamente imutáveis (MappingProxyType, tuple, frozenset), sem compartilhar
    referências com a entrada original.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze_value(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _safe_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        s = _safe_str(item, "")
        if s and s not in result:
            result.append(s)
    return result


# =============================================================================
# Modelos Normalizados de Saída
# =============================================================================

@dataclass(frozen=True)
class NormalizedGlobalStyle:
    visual_language: str = "cinematic_documentary"
    dominant_style: str = "dark_cinematic"
    color_palette: Tuple[str, ...] = ()
    lighting_mood: str = "dark_cinematic"
    pacing_rhythm: str = "deliberate"
    central_theme: str = ""
    production_profile: str = "default"


@dataclass(frozen=True)
class CameraMovementInstruction:
    movement: str = "static"
    kind: str = "static"
    zoom_start: float = 1.0
    zoom_end: float = 1.0
    pan_x: str = "center"
    pan_y: str = "center"
    tilt_deg: float = 0.0
    shake_px: int = 0
    ffmpeg_zoompan_expr: str = ""
    fallback_applied: bool = False


@dataclass(frozen=True)
class NormalizedProductionRoute:
    visual_production_type: str = "auto"
    strategy: str = "auto_cost_hierarchy"
    allow_stock: bool = True
    allow_still_gen: bool = True
    fallback_applied: bool = False


@dataclass(frozen=True)
class NormalizedContinuity:
    """
    Metadados de continuidade visual profundamente imutáveis.
    continuity_notes é exposto como MappingProxyType (somente leitura: use .get(), [], in, .items()).
    Listas aninhadas são convertidas para tuple e dicts aninhados para MappingProxyType.
    """
    continuity_anchor: Optional[str] = None
    visual_connection_previous: Optional[str] = None
    visual_connection_next: Optional[str] = None
    continuity_notes: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class NormalizedSceneDirection:
    scene_id: int
    order: int
    camera_framing: str = "wide"
    camera_movement: CameraMovementInstruction = field(default_factory=CameraMovementInstruction)
    depth_of_field: str = "deep_focus"
    composition: str = "rule_of_thirds"
    lighting_mood: str = "dark_cinematic"
    atmosphere: str = "solemn_tension"
    color_palette_notes: str = ""
    production_route: NormalizedProductionRoute = field(default_factory=NormalizedProductionRoute)
    estimated_cost_tier: int = 1
    narrative_role: str = ""
    narrative_purpose: str = "context"
    visual_intent: str = ""
    what_viewer_sees: str = ""
    dominant_element: str = ""
    transition_in: str = "direct_cut"
    transition_out: str = "crossfade_slow"
    on_screen_text: Optional[str] = None
    continuity: NormalizedContinuity = field(default_factory=NormalizedContinuity)
    original_b_roll_visual: str = ""
    original_b_roll_query: str = ""
    visual_prompt_complement: str = ""


@dataclass(frozen=True)
class InterpretedDirectorPlan:
    enabled: bool
    plan_id: Optional[str] = None
    schema_version: Optional[str] = None
    global_style: NormalizedGlobalStyle = field(default_factory=NormalizedGlobalStyle)
    scenes: Tuple[NormalizedSceneDirection, ...] = ()
    warnings: Tuple[str, ...] = ()
    disabled_reason: Optional[str] = None


# =============================================================================
# Componentes Especializados
# =============================================================================

class GlobalStyleInterpreter:
    """A. Interpreta VisualBible e metadados globais do MasterAVPlan."""

    def interpret(
        self,
        director_metadata: Dict[str, Any],
        master_av_plan_summary: Optional[Dict[str, Any]] = None,
    ) -> NormalizedGlobalStyle:
        summary = master_av_plan_summary if isinstance(master_av_plan_summary, dict) else {}
        vb = director_metadata.get("visual_bible")
        vb_dict = vb if isinstance(vb, dict) else {}

        return NormalizedGlobalStyle(
            visual_language=_safe_str(vb_dict.get("visual_language"), "cinematic_documentary"),
            dominant_style=_safe_str(vb_dict.get("dominant_style"), "dark_cinematic"),
            color_palette=tuple(_safe_str_list(vb_dict.get("color_palette"))),
            lighting_mood=_safe_str(vb_dict.get("lighting_mood"), "dark_cinematic"),
            pacing_rhythm=_safe_str(vb_dict.get("pacing_rhythm"), "deliberate"),
            central_theme=_safe_str(director_metadata.get("central_theme"), ""),
            production_profile=_safe_str(
                director_metadata.get("production_profile") or summary.get("production_profile"),
                "default",
            ),
        )


class CameraMovementMapper:
    """D. Tabela determinística de movimentos de câmera para instruções normalizadas."""

    def map_movement(self, raw_movement: Any) -> CameraMovementInstruction:
        key = _safe_str(raw_movement, "static").lower()
        if key in VALID_CAMERA_MOVEMENTS:
            cfg = CAMERA_MOVEMENT_TABLE[key]
            return CameraMovementInstruction(
                movement=cfg["movement"],
                kind=cfg["kind"],
                zoom_start=cfg["zoom_start"],
                zoom_end=cfg["zoom_end"],
                pan_x=cfg["pan_x"],
                pan_y=cfg["pan_y"],
                tilt_deg=cfg["tilt_deg"],
                shake_px=cfg["shake_px"],
                ffmpeg_zoompan_expr=cfg["ffmpeg_zoompan_expr"],
                fallback_applied=False,
            )

        cfg = CAMERA_MOVEMENT_TABLE["static"]
        return CameraMovementInstruction(
            movement=cfg["movement"],
            kind=cfg["kind"],
            zoom_start=cfg["zoom_start"],
            zoom_end=cfg["zoom_end"],
            pan_x=cfg["pan_x"],
            pan_y=cfg["pan_y"],
            tilt_deg=cfg["tilt_deg"],
            shake_px=cfg["shake_px"],
            ffmpeg_zoompan_expr=cfg["ffmpeg_zoompan_expr"],
            fallback_applied=True,
        )


class VisualProductionRouter:
    """Normaliza o tipo de produção visual sem executar roteamento ou chamadas externas."""

    def normalize_route(self, raw_type: Any) -> NormalizedProductionRoute:
        key = _safe_str(raw_type, "auto").lower()
        if key in VALID_VISUAL_PRODUCTION_TYPES:
            cfg = PRODUCTION_ROUTE_TABLE[key]
            return NormalizedProductionRoute(
                visual_production_type=key,
                strategy=cfg["strategy"],
                allow_stock=cfg["allow_stock"],
                allow_still_gen=cfg["allow_still_gen"],
                fallback_applied=False,
            )

        cfg = PRODUCTION_ROUTE_TABLE["auto"]
        return NormalizedProductionRoute(
            visual_production_type="auto",
            strategy=cfg["strategy"],
            allow_stock=cfg["allow_stock"],
            allow_still_gen=cfg["allow_still_gen"],
            fallback_applied=True,
        )


class ContinuityInterpreter:
    """C. Interpreta metadados de continuidade visual entre cenas."""

    def interpret(
        self,
        op_scene: Dict[str, Any],
        unconsumed_scene: Dict[str, Any],
    ) -> NormalizedContinuity:
        vd_full = unconsumed_scene.get("visual_direction_full")
        vd = vd_full if isinstance(vd_full, dict) else {}

        notes_raw = unconsumed_scene.get("continuity_notes")
        notes = _freeze_value(notes_raw) if isinstance(notes_raw, dict) else MappingProxyType({})

        return NormalizedContinuity(
            continuity_anchor=_safe_opt_str(
                op_scene.get("continuity_anchor") or vd.get("continuity_anchor")
            ),
            visual_connection_previous=_safe_opt_str(vd.get("visual_connection_previous")),
            visual_connection_next=_safe_opt_str(vd.get("visual_connection_next")),
            continuity_notes=notes,
        )


def build_visual_prompt_complement(
    original_prompt: str,
    global_style: NormalizedGlobalStyle,
    lighting_mood: str,
    atmosphere: str,
    color_palette_notes: str,
    visual_intent: str,
    dominant_element: str,
    continuity_anchor: Optional[str],
) -> str:
    """
    Método puro que constrói um complemento textual de direção visual
    sem remover/substituir o prompt original e sem duplicar informações.
    """
    base_lower = (original_prompt or "").lower()
    parts: List[str] = []
    seen_lower = set()

    candidates = [
        ("style", global_style.dominant_style),
        ("palette", ", ".join(global_style.color_palette) if global_style.color_palette else ""),
        ("color_notes", color_palette_notes),
        ("lighting", lighting_mood or global_style.lighting_mood),
        ("atmosphere", atmosphere),
        ("intent", visual_intent),
        ("dominant", dominant_element),
        ("continuity", continuity_anchor or ""),
    ]

    for _, raw_val in candidates:
        val = _safe_str(raw_val, "")
        if not val:
            continue
        val_lower = val.lower()
        if val_lower in base_lower or val_lower in seen_lower:
            continue
        seen_lower.add(val_lower)
        parts.append(val)

    return ", ".join(parts)


class SceneDirectionInterpreter:
    """B. Interpreta a direção visual de cada cena combinando cena operacional e scenes_unconsumed."""

    def __init__(
        self,
        movement_mapper: Optional[CameraMovementMapper] = None,
        production_router: Optional[VisualProductionRouter] = None,
        continuity_interpreter: Optional[ContinuityInterpreter] = None,
    ) -> None:
        self._movement_mapper = movement_mapper or CameraMovementMapper()
        self._production_router = production_router or VisualProductionRouter()
        self._continuity_interpreter = continuity_interpreter or ContinuityInterpreter()

    def interpret_scene(
        self,
        op_scene: Dict[str, Any],
        unconsumed_scene: Dict[str, Any],
        global_style: NormalizedGlobalStyle,
        fallback_order: int = 1,
    ) -> NormalizedSceneDirection:
        vd_full = unconsumed_scene.get("visual_direction_full")
        vd = vd_full if isinstance(vd_full, dict) else {}

        scene_id = _safe_int(
            op_scene.get("scene_id") if op_scene.get("scene_id") is not None else op_scene.get("id"),
            default=fallback_order - 1,
            min_val=0,
        )
        order = _safe_int(op_scene.get("order"), default=fallback_order, min_val=1)

        camera_framing = _safe_str(
            op_scene.get("camera_framing") or vd.get("camera_framing"),
            "wide",
        )
        camera_movement = self._movement_mapper.map_movement(
            op_scene.get("camera_movement") or vd.get("camera_movement")
        )
        production_route = self._production_router.normalize_route(
            op_scene.get("visual_production_type") or vd.get("visual_production_type")
        )
        continuity = self._continuity_interpreter.interpret(op_scene, unconsumed_scene)

        lighting_mood = _safe_str(
            op_scene.get("lighting_mood") or vd.get("lighting_mood") or global_style.lighting_mood,
            "dark_cinematic",
        )
        atmosphere = _safe_str(vd.get("atmosphere"), "solemn_tension")
        color_palette_notes = _safe_str(vd.get("color_palette_notes"), "")
        visual_intent = _safe_str(unconsumed_scene.get("visual_intent"), "")
        dominant_element = _safe_str(vd.get("dominant_element"), "")
        original_b_roll_visual = _safe_str(
            op_scene.get("b_roll_visual") or vd.get("b_roll_visual") or vd.get("what_viewer_sees"),
            "",
        )
        original_b_roll_query = _safe_str(
            op_scene.get("b_roll_query") or vd.get("b_roll_query"),
            "",
        )

        complement = build_visual_prompt_complement(
            original_prompt=original_b_roll_visual,
            global_style=global_style,
            lighting_mood=lighting_mood,
            atmosphere=atmosphere,
            color_palette_notes=color_palette_notes,
            visual_intent=visual_intent,
            dominant_element=dominant_element,
            continuity_anchor=continuity.continuity_anchor,
        )

        return NormalizedSceneDirection(
            scene_id=scene_id,
            order=order,
            camera_framing=camera_framing,
            camera_movement=camera_movement,
            depth_of_field=_safe_str(vd.get("depth_of_field"), "deep_focus"),
            composition=_safe_str(vd.get("composition"), "rule_of_thirds"),
            lighting_mood=lighting_mood,
            atmosphere=atmosphere,
            color_palette_notes=color_palette_notes,
            production_route=production_route,
            estimated_cost_tier=_safe_int(
                op_scene.get("estimated_cost_tier") if op_scene.get("estimated_cost_tier") is not None else vd.get("estimated_cost_tier"),
                default=1,
                min_val=1,
                max_val=5,
            ),
            narrative_role=_safe_str(unconsumed_scene.get("narrative_role"), ""),
            narrative_purpose=_safe_str(op_scene.get("narrative_purpose"), "context"),
            visual_intent=visual_intent,
            what_viewer_sees=_safe_str(vd.get("what_viewer_sees"), ""),
            dominant_element=dominant_element,
            transition_in=_safe_str(op_scene.get("transition_in") or vd.get("transition_in"), "direct_cut"),
            transition_out=_safe_str(op_scene.get("transition_out") or vd.get("transition_out"), "crossfade_slow"),
            on_screen_text=_safe_opt_str(op_scene.get("on_screen_text") or vd.get("on_screen_text")),
            continuity=continuity,
            original_b_roll_visual=original_b_roll_visual,
            original_b_roll_query=original_b_roll_query,
            visual_prompt_complement=complement,
        )


# =============================================================================
# Fachada Principal
# =============================================================================

class DirectorExecutionInterpreter:
    """
    Fachada stateless e determinística para interpretar o MasterAVPlan
    persistido em script_data.
    """

    def __init__(self) -> None:
        self._global_interpreter = GlobalStyleInterpreter()
        self._scene_interpreter = SceneDirectionInterpreter()

    def interpret(self, script_data: Any) -> InterpretedDirectorPlan:
        if not isinstance(script_data, dict):
            reason = "invalid_script_data_type"
            logger.warning("DirectorExecutionInterpreter desativado: %s", reason)
            return InterpretedDirectorPlan(
                enabled=False,
                warnings=(reason,),
                disabled_reason=reason,
            )

        director_metadata = script_data.get("director_metadata")
        if director_metadata is None:
            return InterpretedDirectorPlan(
                enabled=False,
                disabled_reason="missing_director_metadata",
            )

        if not isinstance(director_metadata, dict):
            reason = "invalid_director_metadata_type"
            logger.warning("DirectorExecutionInterpreter desativado: %s", reason)
            return InterpretedDirectorPlan(
                enabled=False,
                warnings=(reason,),
                disabled_reason=reason,
            )

        summary = script_data.get("master_av_plan_summary")
        global_style = self._global_interpreter.interpret(director_metadata, summary)

        raw_scenes = script_data.get("scenes")
        if not isinstance(raw_scenes, list):
            raw_scenes = []

        unconsumed_map = director_metadata.get("scenes_unconsumed")
        if not isinstance(unconsumed_map, dict):
            unconsumed_map = {}

        interpreted_scenes: List[NormalizedSceneDirection] = []
        warnings: List[str] = []

        for idx, raw_scene in enumerate(raw_scenes, start=1):
            if not isinstance(raw_scene, dict):
                warnings.append(f"invalid_scene_entry_at_{idx}")
                continue

            sid = raw_scene.get("scene_id") if raw_scene.get("scene_id") is not None else raw_scene.get("id")
            unconsumed_scene = unconsumed_map.get(str(sid)) if sid is not None else None
            if not isinstance(unconsumed_scene, dict):
                unconsumed_scene = {}

            interpreted_scenes.append(
                self._scene_interpreter.interpret_scene(
                    op_scene=raw_scene,
                    unconsumed_scene=unconsumed_scene,
                    global_style=global_style,
                    fallback_order=idx,
                )
            )

        return InterpretedDirectorPlan(
            enabled=True,
            plan_id=_safe_opt_str(director_metadata.get("plan_id")),
            schema_version=_safe_opt_str(director_metadata.get("schema_version")),
            global_style=global_style,
            scenes=tuple(interpreted_scenes),
            warnings=tuple(warnings),
        )
