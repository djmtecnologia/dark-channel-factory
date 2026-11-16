"""Copywriter local: roteiro matriz no Ollama e localização sem recriar a estrutura."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from uuid import UUID, uuid4

from agents.copywriter.affiliate_links import build_pinned_comment, get_relevant_products
from agents.copywriter.prompts import COPYWRITER_SYSTEM_PROMPT, COPYWRITER_USER_PROMPT_TEMPLATE
from orchestrator.config import get_channel, get_default_channel
from orchestrator.queue_manager import QUEUE_DESIGNER, QUEUE_VIDEO, get_queue_manager
from shared.logger import get_logger
from shared.models import (
    AffiliateLink, AffiliatePlatform, Emotion, NicheData, Scene, SceneType, ScriptData,
)
from shared.ollama_client import OllamaClient, get_ollama_client

logger = get_logger(__name__)

LOCALIZATION_TARGETS = {
    "ai_tools_us": {"language": "en-US", "country": "United States", "currency": "USD"},
    "ai_tools_de": {"language": "de-DE", "country": "Germany", "currency": "EUR"},
    "ai_tools_es": {"language": "es-ES", "country": "Spain", "currency": "EUR"},
    "ai_tools_fr": {"language": "fr-FR", "country": "France", "currency": "EUR"},
}


def _clean_products(products: list[dict]) -> list[dict]:
    return [
        {
            "platform": p.get("platform", "direct"),
            "product_name": p["product_name"],
            "product_description": p.get("product_description", ""),
            "url": p["url"],
            "commission_percent": p.get("commission_percent", 0),
        }
        for p in products
    ]


def generate_matrix_script(
    niche_data: NicheData,
    ollama: OllamaClient | None = None,
) -> tuple[dict, list[dict]]:
    """Gera uma única estrutura narrativa, com buscas visuais neutras em inglês."""
    channel = get_channel(niche_data.channel_id) or get_default_channel()
    products = get_relevant_products(
        [niche_data.primary_keyword, *niche_data.secondary_keywords, *niche_data.lsi_keywords],
        max_products=3,
    )
    product_context = json.dumps(_clean_products(products), ensure_ascii=False)
    prompt = COPYWRITER_USER_PROMPT_TEMPLATE.format(
        niche_data=json.dumps(niche_data.model_dump(mode="json"), ensure_ascii=False, default=str),
        language=niche_data.language or channel.language,
        target_duration=channel.target_duration_s,
        min_scenes=math.ceil(channel.target_duration_s / 25),
        cpm=niche_data.estimated_cpm_usd,
        monetization_products=product_context,
    )
    prompt += """

SINGLE SOURCE OF TRUTH RULES:
- This is the MATRIX SCRIPT. Create the narrative and scene order only once.
- Every scene id, scene_type, duration_s, emotion and b_roll_query is immutable.
- b_roll_query must be concise English and culturally neutral so footage can be reused.
- Return only the JSON object required by the schema."""
    data = (ollama or get_ollama_client()).chat_json(
        COPYWRITER_SYSTEM_PROMPT, prompt, temperature=0.55, num_predict=6000
    )
    return data, products


def localize_script(
    matrix: dict,
    target: dict,
    ollama: OllamaClient | None = None,
) -> dict:
    """Traduz/adapta somente campos textuais, preservando a estrutura do vídeo."""
    immutable = [
        {
            "id": s["id"],
            "scene_type": s.get("scene_type", "solution"),
            "duration_s": s.get("duration_s", 25),
            "emotion": s.get("emotion", "curiosity"),
            "b_roll_query": s.get("b_roll_query", "technology office"),
        }
        for s in matrix.get("scenes", [])
    ]
    prompt = f"""Localize the MATRIX SCRIPT below for {target['country']} ({target['language']}).
Translate and culturally adapt title, description, tags, pinned_comment, thumbnail_brief,
and scene text/on_screen_text. Convert examples, measurements and monetary references to
{target['currency']}. Do not add, remove, merge or reorder scenes. Copy every immutable
field exactly. Keep affiliate URLs unchanged. Return the complete script as JSON.

IMMUTABLE SCENE CONTRACT:
{json.dumps(immutable, ensure_ascii=False)}

MATRIX SCRIPT:
{json.dumps(matrix, ensure_ascii=False)}"""
    localized = (ollama or get_ollama_client()).chat_json(
        "You are a precise audiovisual localization editor. Output JSON only.",
        prompt,
        temperature=0.15,
        num_predict=6000,
    )
    localized_scenes = localized.get("scenes", [])
    if len(localized_scenes) != len(immutable):
        raise ValueError("Localização alterou a quantidade de cenas do roteiro matriz")
    for scene, fixed in zip(localized_scenes, immutable):
        scene.update(fixed)
    return localized


def _to_script_data(
    data: dict,
    niche: NicheData,
    products: list[dict],
    *,
    job_id: UUID,
    source_job_id: UUID,
    channel_id: str,
    language: str,
) -> ScriptData:
    affiliates = []
    for item in data.get("affiliate_links") or _clean_products(products):
        platform_value = str(item.get("platform", "direct")).lower()
        platform = AffiliatePlatform(platform_value) if platform_value in AffiliatePlatform._value2member_map_ else AffiliatePlatform.DIRECT
        affiliates.append(AffiliateLink(
            platform=platform,
            product_name=item["product_name"],
            product_description=item.get("product_description", ""),
            url=item["url"],
            commission_percent=float(item.get("commission_percent", 0)),
        ))
    scenes = []
    for item in data["scenes"]:
        scene_type = str(item.get("scene_type", "solution")).lower()
        emotion = str(item.get("emotion", "curiosity")).lower()
        scenes.append(Scene(
            id=int(item["id"]),
            scene_type=SceneType(scene_type) if scene_type in SceneType._value2member_map_ else SceneType.SOLUTION,
            text=item["text"],
            duration_s=float(item.get("duration_s", 25)),
            emotion=Emotion(emotion) if emotion in Emotion._value2member_map_ else Emotion.CURIOSITY,
            b_roll_query=item.get("b_roll_query", "technology office"),
            on_screen_text=item.get("on_screen_text"),
            sfx_cue=item.get("sfx_cue", "none"),
        ))
    local_niche = niche.model_copy(deep=True)
    local_niche.job_id = job_id
    local_niche.channel_id = channel_id
    local_niche.language = language
    return ScriptData(
        job_id=job_id,
        source_job_id=source_job_id,
        target_channel_id=channel_id,
        niche_data=local_niche,
        title=data["title"][:100],
        description=data.get("description", "")[:5000],
        tags=data.get("tags", [niche.primary_keyword])[:15],
        category_id=str(data.get("category_id", "28")),
        language=language,
        affiliate_links=affiliates,
        pinned_comment=data.get("pinned_comment") or build_pinned_comment(products),
        scenes=scenes,
        total_duration_s=sum(s.duration_s for s in scenes),
        music_mood=data.get("music_mood", "suspense"),
        thumbnail_brief=data.get("thumbnail_brief", data["title"]),
        thumbnail_dominant_color=data.get("thumbnail_dominant_color", "#FF6B35"),
    )


def run_copywriter_agent(niche_data_dict: dict) -> ScriptData:
    """Gera a matriz e despacha quatro localizações que compartilham os B-rolls."""
    niche = NicheData(**niche_data_dict)
    ollama = get_ollama_client()
    if not ollama.healthcheck():
        raise RuntimeError("Ollama indisponível em http://localhost:11434. Execute: ollama serve")

    logger.info("[Copywriter] Gerando roteiro matriz local no Ollama")
    matrix, products = generate_matrix_script(niche, ollama)
    source_job_id = niche.job_id
    localized_scripts: list[ScriptData] = []
    for channel_id, target in LOCALIZATION_TARGETS.items():
        localized = (
            deepcopy(matrix)
            if target["language"] == (niche.language or "en-US")
            else localize_script(matrix, target, ollama)
        )
        localized_scripts.append(_to_script_data(
            localized,
            niche,
            products,
            job_id=source_job_id if not localized_scripts else uuid4(),
            source_job_id=source_job_id,
            channel_id=channel_id,
            language=target["language"],
        ))

    qm = get_queue_manager()
    from agents.designer.agent import run_designer_agent
    from agents.video_editor.agent import run_video_editor_agent
    for script in localized_scripts:
        payload = script.model_dump(mode="json")
        qm.enqueue(QUEUE_VIDEO, run_video_editor_agent, payload, job_timeout=7200)
        qm.enqueue(QUEUE_DESIGNER, run_designer_agent, payload, job_timeout=1800)
    logger.info("[Copywriter] 4 localizações enfileiradas com source_job_id=%s", source_job_id)
    return localized_scripts[0]
