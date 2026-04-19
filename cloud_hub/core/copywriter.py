from typing import Dict, Any
from uuid import UUID
from sqlmodel import Session
from cloud_hub.models import VideoJob, Channel
from cloud_hub.core.monetization import get_best_match_product, inject_tracking_link
from shared_v2.llm_client import get_completion

SYSTEM_PROMPT = """
You are lead Copywriter for a YouTube Dark Channel Factory.
Your goal is to write a high-retention script for a {niche} video in {language}.
The script must include:
1. A powerful hook (first 5 seconds).
2. Deep value content.
3. A CTA (Call to Action) for a link in the first comment.
4. Natural transitions.
"""

async def run_copywriter_phase(session: Session, job: VideoJob):
    """
    Gera o roteiro do vídeo e injeta a monetização.
    """
    channel = session.get(Channel, job.channel_id)
    if not channel:
        return None

    # 1. Monetização: Busca o melhor produto ANTES de escrever o roteiro
    product = get_best_match_product(session, channel.niche, channel.language, job.tenant_id)
    
    affiliate_url = ""
    if product:
        affiliate_url = inject_tracking_link(product, job.id, channel.id)
        job.injected_affiliate_url = affiliate_url
        print(f"💰 Monetization Injected: {product.product_name} -> {affiliate_url}")

    # 2. IA: Escreve o roteiro personalizado
    prompt = f"Write a video script about: {job.topic}.\nAngle: {job.angle}.\nAffiliate CTA: {affiliate_url if affiliate_url else 'None'}"
    
    script_text = get_completion(
        prompt=prompt,
        system_instruction=SYSTEM_PROMPT.format(niche=channel.niche, language=channel.language)
    )

    # 3. Salva os dados no Job
    job.script_data = {
        "title": job.topic,
        "script_full": script_text,
        "cta_link": affiliate_url,
        "generated_at": str(datetime.now(timezone.utc))
    }
    
    # 4. Se o tenant tem auto_approve, já manda pro Worker
    tenant = session.get(Tenant, job.tenant_id)
    if tenant and tenant.auto_approve_content:
        job.status = "APPROVED"
    else:
        job.status = "DRAFTED"
    
    session.add(job)
    session.commit()
    return job
