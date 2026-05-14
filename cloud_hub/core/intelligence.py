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
    
    # [LOGICA REAL COM IA]
    prompt = f"Brainstorm a highly viral, controversial or extremely useful YouTube video topic for the niche '{niche}' in the {market} market. The topic must promise high ROI or a shocking discovery. Return ONLY the catchy title (English US)."
    chosen_topic = get_completion(prompt).strip().replace('"', '')
    
    if "Erro LLM" in chosen_topic:
        chosen_topic = "How AI is Secretly Making Millions in 2026"
    
    # Criar o Job
    job = VideoJob(
        channel_id=channel.id,
        status="PENDING",  # BUG FIX: was 'SCOUTED' which the scheduler never picks up
        topic=chosen_topic
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
        job.status = "PENDING"  # BUG FIX: was 'DRAFTING' which scheduler doesn't watch
    else:
        job.status = "FAILED"
        job.error_message = f"Viral Score Low: {score:.1f}"
        
    session.add(job)
    session.commit()
    return score
