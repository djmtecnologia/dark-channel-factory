import random
from typing import Optional
from uuid import UUID
from sqlmodel import Session, select
from cloud_hub.models import AffiliateProduct, VideoJob

def get_best_match_product(session: Session, niche: str, language: str, tenant_id: UUID) -> Optional[AffiliateProduct]:
    """
    Busca o melhor produto no cache para o nicho e idioma.
    Prioriza ClickBank por Gravity se for Tier 1 (EN/DE/ES), 
    ou Hotmart para nichos específicos.
    """
    # 1. Tenta buscar produtos do tenant específico primeiro
    statement = select(AffiliateProduct).where(
        AffiliateProduct.tenant_id == tenant_id,
        AffiliateProduct.niche == niche,
        AffiliateProduct.language == language
    ).order_by(AffiliateProduct.gravity.desc())
    
    products = session.exec(statement).all()
    
    if not products:
        # 2. Fallback: Busca produtos genéricos/globais para aquele nicho
        # (Em um SaaS real, teríamos uma lista de 'House Offers')
        return None
    
    # Retorna o de maior gravity (ou o primeiro da lista)
    return products[0]

def inject_tracking_link(product: AffiliateProduct, video_job_id: UUID, channel_id: UUID) -> str:
    """
    Injeta o parâmetro de rastreio (TID/SRC) no formato SaaS v2.1:
    [CANAL_ID]-[VIDEO_ID]
    """
    tracking_code = f"{str(channel_id)[:8]}-{str(video_job_id)}"
    
    if product.platform == "CLICKBANK":
        # ClickBank usa 'tid'
        separator = "&" if "?" in product.hotlink_base else "?"
        return f"{product.hotlink_base}{separator}tid={tracking_code}"
    
    elif product.platform == "HOTMART":
        # Hotmart usa 'src'
        separator = "&" if "?" in product.hotlink_base else "?"
        return f"{product.hotlink_base}{separator}src={tracking_code}"
    
    elif product.platform == "AMAZON":
        # Amazon usa 'ascsubtag'
        separator = "&" if "?" in product.hotlink_base else "?"
        return f"{product.hotlink_base}{separator}ascsubtag={tracking_code}"
    
    return product.hotlink_base
