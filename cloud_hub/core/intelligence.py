import os
import random
from typing import List, Dict
from sqlmodel import Session, select
from cloud_hub.models import VideoJob, Channel
from shared.llm_client import get_completion # Vamos precisar mover o shared_v2

async def run_scout_phase(session: Session, channel: Channel):
    """
    Simula a fase de Scout & Miner no Cloud.
    Encontra tópicos quentes no nicho do canal.
    """
    niche = channel.niche
    market = channel.market_region
    
    # [LOGICA REAL AQUI - Pytrends / YouTube API]
    # Por agora, usamos a lógica de autonomia para gerar um tópico lucrativo
    topics_pool = {
        "ai_tools": ["How to automate your business with 3 free AI tools", "Top 5 AI Chrome Extensions for productivity"],
        "personal_finance": ["How to invest your first $1000 in 2026", "The truth about passive income in Tier 1 markets"],
        "psychology_curiosity": ["The psychology of winning 99% of arguments", "3 habits that destroy 50% of your focus"],
        "health_longevity": ["Scientific way to sleep 6 hours and feel like 8", "10-minute daily routine to live longer"]
    }
    
    chosen_topic = random.choice(topics_pool.get(niche, ["Trending topic in " + niche]))
    
    # Criar o Job
    job = VideoJob(
        tenant_id=channel.tenant_id,
        channel_id=channel.id,
        status="SCOUTED",
        topic=chosen_topic,
        angle="High Retention / Listicle",
        target_keywords=[niche, "2026", "automation"]
    )
    
    session.add(job)
    session.commit()
    session.refresh(job)
    return job

async def run_viral_score_phase(session: Session, job: VideoJob):
    """
    Avalia o potencial viral do tópico escolhido.
    Score < 70 aborta o job.
    """
    # Lógica de simulação de score viral (depois integrada ao LLM)
    # Aqui iriam dados de volume de busca vs competitividade
    score = random.uniform(70, 95) # Simulação passando no gate
    
    job.viral_score = score
    if score >= 70:
        job.status = "DRAFTING"
    else:
        job.status = "FAILED"
        job.error_message = f"Viral Score Low: {score:.1f}"
        
    session.add(job)
    session.commit()
    return score
