"""
Copywriter Agent — Dark Channel Factory
Agent 2: Gera roteiro completo, metadados SEO e links afiliados.
Publica ScriptData para as filas 'video' e 'designer'.
"""
from __future__ import annotations

import json
from uuid import UUID

from dotenv import load_dotenv

from agents.copywriter.affiliate_links import build_pinned_comment, get_relevant_products
from agents.copywriter.prompts import COPYWRITER_SYSTEM_PROMPT, COPYWRITER_USER_PROMPT_TEMPLATE
from orchestrator.config import get_channel, get_default_channel
from orchestrator.queue_manager import QUEUE_DESIGNER, QUEUE_VIDEO, get_queue_manager
from shared.llm_client import get_llm_client
from shared.logger import get_logger
from shared.models import (
    AffiliateLink,
    AffiliatePlatform,
    NicheData,
    Scene,
    SceneType,
    Emotion,
    ScriptData,
)

load_dotenv()
logger = get_logger(__name__)


def run_copywriter_agent(niche_data_dict: dict) -> ScriptData:
    """
    Executa o Copywriter Agent.

    Args:
        niche_data_dict: NicheData serializado como dict (vindo da fila RQ)
    """
    niche_data = NicheData(**niche_data_dict)
    logger.info(f"✍️ [Copywriter] Starting | job={niche_data.job_id} | topic='{niche_data.topic}'")

    channel = get_channel(niche_data.channel_id) or get_default_channel()
    llm = get_llm_client()

    # ── Step 1: Selecionar produtos afiliados ──────────────────
    all_kws = [niche_data.primary_keyword] + niche_data.secondary_keywords + niche_data.lsi_keywords
    affiliate_products = get_relevant_products(all_kws, max_products=3)
    logger.info(f"💰 [Copywriter] Selected {len(affiliate_products)} affiliate products")

    # ── Step 2: Montar prompt ──────────────────────────────────
    user_prompt = COPYWRITER_USER_PROMPT_TEMPLATE.format(
        niche_data=json.dumps(niche_data.model_dump(mode="json"), indent=2),
        language=niche_data.language or channel.language,
        target_duration=channel.target_duration_s,
        target_minutes=channel.target_duration_s // 60,
        cpm=niche_data.estimated_cpm_usd,
        affiliate_category=niche_data.topic,
    )

    # ── Step 3: Chamar LLM ──────────────────────────────────────
    logger.info("🤖 [Copywriter] Calling LLM...")
    llm_response = llm.complete(
        system_prompt=COPYWRITER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.75,
        max_tokens=6000,
        json_mode=True,
    )

    # ── Step 4: Parsear resposta ────────────────────────────────
    try:
        raw = llm_response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"[Copywriter] JSON parse failed: {e}")
        raise

    # ── Step 5: Construir objetos Pydantic ─────────────────────
    # Affiliate Links
    affiliate_links: list[AffiliateLink] = []

    # Tenta usar os da resposta LLM primeiro
    llm_affiliates = data.get("affiliate_links", [])
    if llm_affiliates:
        for item in llm_affiliates:
            try:
                platform_str = item.get("platform", "direct").lower()
                platform = AffiliatePlatform(platform_str) if platform_str in AffiliatePlatform._value2member_map_ else AffiliatePlatform.DIRECT
                affiliate_links.append(AffiliateLink(
                    platform=platform,
                    product_name=item["product_name"],
                    product_description=item.get("product_description", ""),
                    url=item["url"],
                    commission_percent=float(item.get("commission_percent", 0)),
                ))
            except Exception:
                pass

    # Preenche com produtos selecionados se o LLM não gerou suficiente
    if len(affiliate_links) < 2:
        for p in affiliate_products:
            platform_str = p["platform"]
            platform = AffiliatePlatform(platform_str) if platform_str in AffiliatePlatform._value2member_map_ else AffiliatePlatform.DIRECT
            affiliate_links.append(AffiliateLink(
                platform=platform,
                product_name=p["product_name"],
                product_description=p["product_description"],
                url=p["url"],
                commission_percent=p["commission_percent"],
            ))

    # Pinned Comment
    pinned = data.get("pinned_comment") or build_pinned_comment(affiliate_products)

    # Scenes
    scenes: list[Scene] = []
    for sc in data.get("scenes", []):
        try:
            scene_type_str = sc.get("scene_type", "solution").lower()
            scene_type = SceneType(scene_type_str) if scene_type_str in SceneType._value2member_map_ else SceneType.SOLUTION
            emotion_str = sc.get("emotion", "curiosity").lower()
            emotion = Emotion(emotion_str) if emotion_str in Emotion._value2member_map_ else Emotion.CURIOSITY

            scenes.append(Scene(
                id=sc["id"],
                scene_type=scene_type,
                text=sc["text"],
                duration_s=float(sc.get("duration_s", 30)),
                emotion=emotion,
                b_roll_query=sc.get("b_roll_query", "technology office laptop"),
                on_screen_text=sc.get("on_screen_text"),
                sfx_cue=sc.get("sfx_cue", "none"),
            ))
        except Exception as e:
            logger.warning(f"[Copywriter] Skipping malformed scene {sc.get('id', '?')}: {e}")

    total_duration = sum(s.duration_s for s in scenes) or float(data.get("total_duration_s", channel.target_duration_s))

    script_data = ScriptData(
        job_id=niche_data.job_id,
        niche_data=niche_data,
        title=data["title"][:100],
        description=data.get("description", "")[:5000],
        tags=data.get("tags", [niche_data.primary_keyword])[:15],
        category_id=data.get("category_id", "28"),
        language=niche_data.language or channel.language,
        affiliate_links=affiliate_links,
        pinned_comment=pinned,
        scenes=scenes,
        total_duration_s=total_duration,
        music_mood=data.get("music_mood", "suspense"),
        thumbnail_brief=data.get("thumbnail_brief", f"Bold text '{data['title'][:20]}' on dark background, shocking expression"),
        thumbnail_dominant_color=data.get("thumbnail_dominant_color", "#FF6B35"),
    )

    logger.info(
        f"✅ [Copywriter] ScriptData ready | title='{script_data.title}' | "
        f"scenes={len(script_data.scenes)} | duration={int(total_duration)}s"
    )

    # ── Step 6: Enfileirar Video Editor E Designer em paralelo ──
    from agents.video_editor.agent import run_video_editor_agent
    from agents.designer.agent import run_designer_agent

    qm = get_queue_manager()
    script_dict = script_data.model_dump(mode="json")

    qm.enqueue(QUEUE_VIDEO, run_video_editor_agent, script_dict, job_timeout=3600)
    qm.enqueue(QUEUE_DESIGNER, run_designer_agent, script_dict, job_timeout=1800)

    logger.info(f"→ [Copywriter] ScriptData enqueued to '{QUEUE_VIDEO}' and '{QUEUE_DESIGNER}'")
    return script_data
