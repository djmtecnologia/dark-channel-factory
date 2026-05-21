"""
Scout & Miner Agent — Dark Channel Factory
Agent 1: Identifica nicho, tópico e keywords. Publica na fila 'copywriter'.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

from dotenv import load_dotenv

from agents.scout_miner.prompts import (
    DEEP_DIVE_ADDENDUM,
    SCOUT_SYSTEM_PROMPT,
    SCOUT_USER_PROMPT_TEMPLATE,
)
from agents.scout_miner.youtube_trends import collect_trend_data
from orchestrator.config import get_channel, get_default_channel
from orchestrator.queue_manager import QUEUE_COPYWRITER, get_queue_manager
from shared.logger import get_logger
from shared.llm_client import get_llm_client
from shared.models import NicheData, Niche

load_dotenv()
logger = get_logger(__name__)


def run_scout_agent(
    job_id: str,
    channel_id: str = "ai_tools_us",
    is_deep_dive: bool = False,
    parent_topic: str | None = None,
    parent_video_id: str | None = None,
    parent_retention: float = 0.0,
) -> NicheData:
    """
    Executa o Scout & Miner Agent.
    Coleta dados de tendências, chama o LLM e enfileira NicheData para o Copywriter.

    Args:
        job_id: UUID do job (string)
        channel_id: ID do canal configurado
        is_deep_dive: Se True, gera conteúdo aprofundado sobre parent_topic
        parent_topic: Tópico do vídeo pai (apenas para deep dives)
        parent_video_id: YouTube video ID do vídeo pai
        parent_retention: Taxa de retenção do vídeo pai (%)
    """
    logger.info(f"🔍 [Scout] Starting | job={job_id} | channel={channel_id} | deep_dive={is_deep_dive}")

    channel = get_channel(channel_id) or get_default_channel()
    llm = get_llm_client()

    # ── Step 1: Coletar dados de trending ─────────────────────
    trend_data = collect_trend_data(language=channel.language)
    logger.info(f"📈 [Scout] Trend data collected: {len(trend_data.get('trending_queries', {}).get('rising', []))} rising queries")

    # ── Step 2: Formatar prompt ─────────────────────────────────
    user_prompt = SCOUT_USER_PROMPT_TEMPLATE.format(
        trending_data=json.dumps(trend_data["trending_queries"], indent=2),
        competition_data=json.dumps(trend_data["competition_analysis"], indent=2),
        language=channel.language,
        is_deep_dive=is_deep_dive,
        parent_topic=parent_topic or "N/A",
    )

    if is_deep_dive and parent_topic:
        user_prompt += DEEP_DIVE_ADDENDUM.format(
            parent_topic=parent_topic,
            retention=parent_retention,
        )

    # ── Step 3: Chamar LLM ──────────────────────────────────────
    logger.info(f"🤖 [Scout] Calling LLM...")
    llm_response = llm.complete(
        system_prompt=SCOUT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.8,
        max_tokens=4096,
        json_mode=True,
    )

    # ── Step 4: Parsear resposta (Defensivo + Reparo de Truncamento) ──
    raw = llm_response.text.strip()
    
    # 1. Limpeza de Markdown Code Fences
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
        
    start_idx = raw.find('{')
    if start_idx != -1:
        end_idx = raw.rfind('}')
        if end_idx != -1 and end_idx > start_idx:
            raw = raw[start_idx:end_idx+1]
        else:
            raw = raw[start_idx:]
        
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[Scout] Initial JSON parse failed: {e}. Attempting automated heuristic repair...")
        
        # Reparo Rápido de Truncamento de String
        candidate = raw
        if candidate.count('"') % 2 != 0:
            candidate += '"'
            
        # Fechamento balanceado de Chaves e Colchetes
        brace_count = candidate.count('{') - candidate.count('}')
        bracket_count = candidate.count('[') - candidate.count(']')
        
        if bracket_count > 0:
            candidate += ']' * bracket_count
        if brace_count > 0:
            candidate += '}' * brace_count
            
        try:
            data = json.loads(candidate)
            logger.info("✅ [Scout] JSON successfully repaired and parsed!")
        except json.JSONDecodeError as final_err:
            logger.error(f"[Scout] Critical JSON parse failure after repair: {final_err}. Using regex/default fallback...")
            
            import re
            topic_match = re.search(r'"topic"\s*:\s*"([^"]+)"', raw)
            kw_match = re.search(r'"primary_keyword"\s*:\s*"([^"]+)"', raw)
            angle_match = re.search(r'"angle"\s*:\s*"([^"]+)"', raw)
            
            topic = topic_match.group(1) if topic_match else ("Como a IA está mudando o mundo" if "pt" in channel.language else "How AI is Changing the World")
            kw = kw_match.group(1) if kw_match else ("inteligência artificial" if "pt" in channel.language else "artificial intelligence")
            angle = angle_match.group(1) if angle_match else ("educacional" if "pt" in channel.language else "educational")
            
            data = {
                "topic": topic,
                "primary_keyword": kw,
                "angle": angle,
                "secondary_keywords": [],
                "lsi_keywords": [],
                "content_gap": "Desconhecido",
                "target_audience": "professionals",
                "estimated_cpm_usd": 25.0,
                "niche_score": 75,
                "competition_level": "medium"
            }
            logger.info("✅ [Scout] Successfully extracted basic fields using regex backup!")

    # ── Step 5: Construir NicheData ─────────────────────────────
    niche_data = NicheData(
        job_id=UUID(job_id),
        topic=data.get("topic", "Default Topic"),
        language=channel.language,
        voice=channel.voice,
        niche=Niche(channel.niche),
        primary_keyword=data.get("primary_keyword", "AI"),
        secondary_keywords=data.get("secondary_keywords", []),
        lsi_keywords=data.get("lsi_keywords", []),
        angle=data.get("angle", "Default Angle"),
        content_gap=data.get("content_gap", ""),
        target_audience=data.get("target_audience", "professionals aged 25-45"),
        estimated_cpm_usd=float(data.get("estimated_cpm_usd", 25.0)),
        niche_score=int(data.get("niche_score", 75)),
        competition_level=data.get("competition_level", "medium"),
        is_deep_dive=is_deep_dive,
        parent_video_id=parent_video_id,
        parent_topic=parent_topic,
        channel_id=channel_id,
    )

    logger.info(
        f"✅ [Scout] NicheData ready | topic='{niche_data.topic}' | "
        f"score={niche_data.niche_score} | CPM=${niche_data.estimated_cpm_usd}"
    )

    # ── Step 6: Enfileirar para Copywriter ──────────────────────
    from agents.copywriter.agent import run_copywriter_agent

    qm = get_queue_manager()
    qm.enqueue(
        QUEUE_COPYWRITER,
        run_copywriter_agent,
        niche_data.model_dump(mode="json"),
        job_timeout=1200,
    )
    logger.info(f"→ [Scout] NicheData enqueued to '{QUEUE_COPYWRITER}'")

    return niche_data
