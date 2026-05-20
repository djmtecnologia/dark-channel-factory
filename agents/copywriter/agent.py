"""
Copywriter Agent — Dark Channel Factory
Agent 2: Gera roteiro completo, metadados SEO e links afiliados.
Implementa engenharia defensiva, RAG estruturado e loop Generator-Critic de auto-correção.
"""
from __future__ import annotations

import json
import traceback
from uuid import UUID

from dotenv import load_dotenv

from agents.copywriter.affiliate_links import build_pinned_comment, get_relevant_products
from agents.copywriter.prompts import COPYWRITER_SYSTEM_PROMPT, COPYWRITER_USER_PROMPT_TEMPLATE
from agents.content_validator.agent import run_content_validator_agent
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
    Executa o Copywriter Agent com um loop Generator-Critic de auto-correção.

    Args:
        niche_data_dict: NicheData serializado como dict (vindo da fila RQ)
    """
    niche_data = NicheData(**niche_data_dict)
    logger.info(f"✍️ [Copywriter] Starting | job={niche_data.job_id} | topic='{niche_data.topic}'")

    channel = get_channel(niche_data.channel_id) or get_default_channel()
    llm = get_llm_client()

    # ── Step 1: Selecionar produtos afiliados (RAG Grounding Context) ──────────────────
    all_kws = [niche_data.primary_keyword] + niche_data.secondary_keywords + niche_data.lsi_keywords
    affiliate_products = get_relevant_products(all_kws, max_products=3)
    logger.info(f"💰 [Copywriter RAG] Selected {len(affiliate_products)} affiliate products for grounding")

    # Formata produtos em uma string estruturada XML para RAG limpo
    monetization_products = ""
    for idx, p in enumerate(affiliate_products, 1):
        monetization_products += (
            f"PRODUCT {idx}:\n"
            f"- Name: {p['product_name']}\n"
            f"- Description: {p['product_description']}\n"
            f"- Direct URL: {p['url']}\n"
            f"- Commission: {p['commission_percent']}%\n\n"
        )

    # ── Step 2: Loop Generator-Critic de Auto-Correção (Self-Healing Loop) ──────────────
    max_attempts = 4
    attempt_feedback = ""
    best_script_data: ScriptData | None = None
    best_originality_score = 0.0

    for attempt in range(1, max_attempts + 1):
        logger.info(f"🔄 [Copywriter Generator] Generation Attempt {attempt}/{max_attempts}...")

        # Monta prompt aplicando as diretrizes de delimitação XML e RAG
        user_prompt = COPYWRITER_USER_PROMPT_TEMPLATE.format(
            niche_data=json.dumps(niche_data.model_dump(mode="json"), indent=2),
            language=niche_data.language or channel.language,
            target_duration=channel.target_duration_s,
            cpm=niche_data.estimated_cpm_usd,
            monetization_products=monetization_products.strip() or "No affiliate products available.",
        )

        # Injeta o feedback crítico se estiver em uma tentativa de re-geração
        if attempt_feedback:
            user_prompt += (
                f"\n\n<validation_feedback>\n"
                f"Your previous attempt FAILED validation. Please fix these issues:\n"
                f"{attempt_feedback}\n"
                f"</validation_feedback>\n"
                f"Regenerate the COMPLETE script, resolving ALL issues and preserving valid structures."
            )

        # Chamada do LLM com Temperatura balanceada (0.65 para criatividade narrativa ancorada)
        try:
            llm_response = llm.complete(
                system_prompt=COPYWRITER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.65,
                max_tokens=6000,
                json_mode=True,
            )
            raw = llm_response.text.strip()
        except Exception as llm_err:
            logger.error(f"❌ [Copywriter LLM Error] Attempt {attempt} failed: {llm_err}")
            attempt_feedback = f"LLM API failure encountered: {str(llm_err)}"
            continue

        # Limpeza básica de Markdown blocks se o LLM ignorar o json_mode
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        
        # ── Step 3: Validação Estruturada (JSON & Pydantic Typings) ─────────────────
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as json_err:
            logger.warning(f"⚠️ [Copywriter JSON Error] Invalid JSON in attempt {attempt}: {json_err}")
            attempt_feedback = f"JSON decoding failed: {str(json_err)}. Ensure you output pure, parseable JSON only."
            continue

        # Inicia a construção dos objetos validados Pydantic para tipagem forte
        try:
            # Affiliate Links
            affiliate_links: list[AffiliateLink] = []
            llm_affiliates = data.get("affiliate_links", [])
            if llm_affiliates:
                for item in llm_affiliates:
                    platform_str = item.get("platform", "direct").lower()
                    platform = AffiliatePlatform(platform_str) if platform_str in AffiliatePlatform._value2member_map_ else AffiliatePlatform.DIRECT
                    affiliate_links.append(AffiliateLink(
                        platform=platform,
                        product_name=item["product_name"],
                        product_description=item.get("product_description", ""),
                        url=item["url"],
                        commission_percent=float(item.get("commission_percent", 0)),
                    ))

            # Caso faltem links na resposta, ancora nos produtos recomendados pelo RAG
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

            pinned = data.get("pinned_comment") or build_pinned_comment(affiliate_products)

            # Scenes
            scenes: list[Scene] = []
            for sc in data.get("scenes", []):
                scene_type_str = sc.get("scene_type", "solution").lower()
                scene_type = SceneType(scene_type_str) if scene_type_str in SceneType._value2member_map_ else SceneType.SOLUTION
                emotion_str = sc.get("emotion", "curiosity").lower()
                emotion = Emotion(emotion_str) if emotion_str in Emotion._value2member_map_ else Emotion.CURIOSITY

                scenes.append(Scene(
                    id=sc["id"],
                    scene_type=scene_type,
                    text=sc["text"],
                    duration_s=float(sc.get("duration_s", 25)),
                    emotion=emotion,
                    b_roll_query=sc.get("b_roll_query", "technology business automation"),
                    on_screen_text=sc.get("on_screen_text"),
                    sfx_cue=sc.get("sfx_cue", "none"),
                ))

            total_duration = sum(s.duration_s for s in scenes) or float(data.get("total_duration_s", channel.target_duration_s))

            # Instanciação Pydantic - Garante consistência total de tipos e valores
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

        except Exception as pydantic_err:
            logger.warning(f"⚠️ [Copywriter Pydantic Error] Typing validation failed in attempt {attempt}: {pydantic_err}")
            attempt_feedback = f"Pydantic Validation failed: {str(pydantic_err)}. Ensure all fields like 'scenes', 'title', and 'description' conform strictly to the required types."
            continue

        # ── Step 4: Orquestração Multi-Agente (Critic Agent Evaluation) ─────────────
        logger.info("🕵️ [Critic Agent] Invoking Content Validator for cross-checking...")
        
        # Executa o Critic Agent de forma síncrona sem afetar a fila ou gravar erro definitivo no banco de dados
        critic_result = run_content_validator_agent(
            script_data.model_dump(mode="json"),
            save_db=False,
            forward=False
        )

        if critic_result.passed:
            logger.info("🎉 [Critic Agent] Script approved and verified successfully!")
            best_script_data = script_data
            break
        else:
            logger.warning(
                f"❌ [Critic Agent] Script REJECTED in attempt {attempt} | "
                f"Originality={critic_result.originality_score} | Narrative={critic_result.narrative_quality_score} | Value={critic_result.transformative_value_score}"
            )
            
            # Compila o feedback do avaliador para alimentar a próxima tentativa do gerador
            feedback_issues = "\n".join(f"- CRITICAL ISSUE: {issue}" for issue in critic_result.issues)
            feedback_suggestions = "\n".join(f"- SUGGESTION: {sug}" for sug in critic_result.suggestions)
            
            attempt_feedback = (
                f"The Critic Agent audited your work and found the following errors:\n"
                f"{feedback_issues}\n"
                f"{feedback_suggestions}\n"
                f"Refactor and correct your narration text, pacing, hook and structure in the next iteration."
            )

            # Salva o melhor script gerado em caso de estourar as tentativas
            if critic_result.originality_score > best_originality_score:
                best_originality_score = critic_result.originality_score
                best_script_data = script_data

    # ── Step 5: Fallback de Segurança se todas as tentativas falharem ──────────────
    if not best_script_data:
        logger.critical("🚨 [Copywriter Failure] All generation attempts failed JSON/Pydantic validation! Aborting.")
        raise RuntimeError("Copywriter failed to produce a valid ScriptData object after multiple attempts.")

    # Se passamos mas com avisos/rejeição nas retries, usamos a melhor versão salva no loop
    script_data = best_script_data
    logger.info(
        f"✅ [Copywriter] Finalized Script | title='{script_data.title}' | "
        f"scenes={len(script_data.scenes)} | duration={int(script_data.total_duration_s)}s"
    )

    # ── Step 6: Enfileirar Video Editor E Designer em paralelo ───────────────────
    from agents.video_editor.agent import run_video_editor_agent
    from agents.designer.agent import run_designer_agent

    qm = get_queue_manager()
    script_dict = script_data.model_dump(mode="json")

    qm.enqueue(QUEUE_VIDEO, run_video_editor_agent, script_dict, job_timeout=3600)
    qm.enqueue(QUEUE_DESIGNER, run_designer_agent, script_dict, job_timeout=1800)

    logger.info(f"→ [Copywriter] Dispatching approved job to '{QUEUE_VIDEO}' and '{QUEUE_DESIGNER}'")
    return script_data
