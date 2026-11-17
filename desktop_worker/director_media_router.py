"""
Director Media Router — Sprint 2.6 (Tarefa 2)

Camada de integração entre o DirectorExecutionInterpreter e o Desktop Node
para orientar a seleção de mídia e o Hook Engine da Cena 0, preservando
100% do comportamento legado quando o Director está desabilitado ou ausente.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Dict, Optional, Set, Tuple

from desktop_worker.director_execution_interpreter import (
    DirectorExecutionInterpreter,
    InterpretedDirectorPlan,
    NormalizedGlobalStyle,
    NormalizedSceneDirection,
    VALID_VISUAL_PRODUCTION_TYPES,
)

logger = logging.getLogger(__name__)


def interpret_director_plan_safe(script_data: Any) -> InterpretedDirectorPlan:
    """
    Instancia o DirectorExecutionInterpreter e interpreta script_data uma única vez
    por job de forma segura. Nunca lança exceção.
    """
    try:
        interpreter = DirectorExecutionInterpreter()
        plan = interpreter.interpret(script_data)
        if plan.enabled:
            logger.info(
                "director_plan_interpreted",
                extra={
                    "plan_id": plan.plan_id,
                    "scenes_count": len(plan.scenes),
                    "warnings_count": len(plan.warnings),
                },
            )
        else:
            logger.info(
                "director_plan_disabled",
                extra={"reason": plan.disabled_reason or "unknown"},
            )
        return plan
    except Exception as exc:
        logger.warning(
            "director_plan_interpretation_failed",
            extra={"error_type": type(exc).__name__},
        )
        return InterpretedDirectorPlan(
            enabled=False,
            disabled_reason=f"interpreter_exception:{type(exc).__name__}",
        )


def build_scene_direction_index(
    plan: InterpretedDirectorPlan,
) -> Tuple[Dict[int, NormalizedSceneDirection], Dict[int, NormalizedSceneDirection]]:
    """
    Constrói índices por scene_id e por order para associação rápida e determinística.
    """
    by_scene_id: Dict[int, NormalizedSceneDirection] = {}
    by_order: Dict[int, NormalizedSceneDirection] = {}
    if not plan.enabled:
        return by_scene_id, by_order

    for direction in plan.scenes:
        by_scene_id[direction.scene_id] = direction
        by_order[direction.order] = direction
    return by_scene_id, by_order


def resolve_scene_direction(
    plan: InterpretedDirectorPlan,
    by_scene_id: Dict[int, NormalizedSceneDirection],
    by_order: Dict[int, NormalizedSceneDirection],
    scene: Dict[str, Any],
    scene_index: int,
) -> Optional[NormalizedSceneDirection]:
    """
    Associa a NormalizedSceneDirection correta à cena atual.
    Prioriza scene_id/id explícito; faz fallback para order (scene_index + 1).
    """
    if not plan.enabled or not isinstance(scene, dict):
        return None

    raw_sid = scene.get("scene_id") if scene.get("scene_id") is not None else scene.get("id")
    if raw_sid is not None and not isinstance(raw_sid, bool):
        try:
            sid_int = int(raw_sid)
            if sid_int in by_scene_id:
                return by_scene_id[sid_int]
        except (TypeError, ValueError):
            pass

    return by_order.get(scene_index + 1)


def build_hook_prompt(
    topic: str,
    scene: Dict[str, Any],
    scene_direction: Optional[NormalizedSceneDirection],
    global_style: Optional[NormalizedGlobalStyle] = None,
) -> Tuple[str, bool]:
    """
    Constrói o prompt do Hook Engine (Cena 0).
    - Se scene_direction estiver presente: combina b_roll_visual da Cena 0,
      visual_prompt_complement, VisualBible (global_style) e continuity_anchor.
    - Caso contrário: retorna exatamente o prompt legado.
    """
    if scene_direction is None:
        legacy_prompt = (
            f"A dramatic cinematic premium illustration representing: '{topic}', "
            "hyper-realistic key art, epic style, intense red glowing accent lights, "
            "high contrast, masterpiece, trending on artstation"
        )
        return legacy_prompt, False

    base_visual = (
        scene_direction.original_b_roll_visual
        or (scene.get("b_roll_visual") if isinstance(scene, dict) else "")
        or topic
    )
    parts = [str(base_visual).strip()]

    complement = (scene_direction.visual_prompt_complement or "").strip()
    if complement:
        parts.append(complement)

    anchor = scene_direction.continuity.continuity_anchor
    if anchor and anchor.strip() and anchor.strip().lower() not in complement.lower():
        parts.append(f"continuity anchor: {anchor.strip()}")

    if global_style is not None:
        style_tag = f"{global_style.visual_language}, {global_style.dominant_style}".strip(", ")
        if style_tag and style_tag.lower() not in complement.lower():
            parts.append(style_tag)

    directed_prompt = ", ".join(p for p in parts if p)
    return directed_prompt, True


def select_media_with_direction(
    *,
    visual_kw: str,
    scene_text: str,
    scene_index: int,
    scene_direction: Optional[NormalizedSceneDirection],
    used_assets: Set[str],
    assets_cache_dir: str,
    fetch_broll_fn: Callable[[str, Set[str]], Optional[str]],
    generate_pollinations_fn: Callable[[str, str], bool],
    find_local_cache_fn: Optional[Callable[[str, Set[str]], Optional[str]]] = None,
    hook_prompt: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """
    Roteia a seleção de mídia com base em visual_production_type e estimated_cost_tier (1..5).
    Hierarquia estrita de custo:
      1. Biblioteca local e cache
      2. Stock (Pexels/Pixabay via fetch_broll_fn)
      3. Pollinations (somente se permitido e necessário)
    Retorna (caminho_do_asset, estrategia_utilizada).
    """
    if scene_direction is None:
        return fetch_broll_fn(visual_kw, used_assets), "legacy"

    prod_type = scene_direction.production_route.visual_production_type
    if prod_type not in VALID_VISUAL_PRODUCTION_TYPES:
        prod_type = "auto"

    # Contrato MasterAVPlan: estimated_cost_tier é inteiro 1..5 (default 1).
    raw_tier = scene_direction.estimated_cost_tier
    cost_tier = raw_tier if isinstance(raw_tier, int) and not isinstance(raw_tier, bool) and 1 <= raw_tier <= 5 else 1

    logger.info(
        "director_media_routing",
        extra={
            "scene_index": scene_index,
            "scene_id": scene_direction.scene_id,
            "visual_production_type": prod_type,
            "estimated_cost_tier": cost_tier,
        },
    )

    # 1. Biblioteca local e cache sempre têm prioridade máxima
    if find_local_cache_fn is not None:
        local_hit = find_local_cache_fn(visual_kw, used_assets)
        if local_hit:
            return local_hit, f"{prod_type}:local_cache_hit"

    if prod_type == "local_asset":
        # Prioriza recursos locais; fallback seguro para stock sem geração paga/externa extra
        return fetch_broll_fn(visual_kw, used_assets), "local_asset:stock_fallback"

    if prod_type == "cinematic_still":
        # Contrato MasterAVPlan define estimated_cost_tier como inteiro 1..5 (default 1)
        # sem limiares de geração por nível; portanto aplicamos a hierarquia padrão
        # segura de forma uniforme para todos os níveis válidos 1..5.
        if hook_prompt:
            still_prompt = hook_prompt
        else:
            prompt_parts = [visual_kw]
            if scene_direction.visual_prompt_complement:
                prompt_parts.append(scene_direction.visual_prompt_complement)
            still_prompt = ", ".join(p for p in prompt_parts if p)

        safe_id = hashlib.md5(f"{still_prompt}_{scene_index}".encode()).hexdigest()[:12]
        still_path = os.path.join(assets_cache_dir, f"director_still_{safe_id}.jpg")

        # 2. Cache de assets existentes (incluindo director_still_*.jpg) ANTES de Pexels/Pixabay
        if os.path.exists(still_path) and os.path.getsize(still_path) > 15000 and still_path not in used_assets:
            return still_path, "cinematic_still:still_cache_hit"

        # 3. Consultar stock (Pexels/Pixabay) antes de qualquer geração por IA
        stock_hit = fetch_broll_fn(visual_kw, used_assets)
        if stock_hit:
            return stock_hit, "cinematic_still:stock_hit"

        # 4. Pollinations somente quando necessário e permitido
        if scene_direction.production_route.allow_still_gen:
            try:
                if generate_pollinations_fn(still_prompt, still_path):
                    return still_path, "cinematic_still:pollinations"
            except Exception as exc:
                logger.warning(
                    "director_pollinations_failed_fallback",
                    extra={"scene_index": scene_index, "error_type": type(exc).__name__},
                )

        # 5. Fallback seguro
        return None, "cinematic_still:fallback_unavailable"

    if prod_type in ("stock_footage", "motion_graphic", "blender_3d", "auto"):
        # stock_footage: prioriza stock
        # motion_graphic / blender_3d: fallback seguro para stock/imagem estática (Blender desabilitado)
        # auto: mantém hierarquia atual
        return fetch_broll_fn(visual_kw, used_assets), f"{prod_type}:broll"

    return fetch_broll_fn(visual_kw, used_assets), "auto:broll"
