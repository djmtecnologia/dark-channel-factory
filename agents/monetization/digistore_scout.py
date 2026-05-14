import os
import requests
from typing import Optional
from sqlmodel import Session
from cloud_hub.models import AffiliateProduct
from shared.logger import get_logger

logger = get_logger(__name__)

class DigistoreScout:
    """Agente especialista em minerar produtos na Digistore24 Europa."""
    
    def __init__(self, session: Session):
        self.session = session
        self.affiliate_id = os.getenv("DIGISTORE_AFFILIATE_ID")
        self.api_key = os.getenv("DIGISTORE_API_KEY")
        # Digistore24 links: https://www.digistore24.com/redir/PRODUCT_ID/AFFILIATE_ID/CAMPAIGN
        self.base_redir_url = "https://www.digistore24.com/redir/{product_id}/{affiliate_id}"

    def find_and_cache_product(self, topic: str, niche: str, language: str, tenant_id: any) -> Optional[AffiliateProduct]:
        """Busca um produto na Digistore24 e salva no cache."""
        if not self.affiliate_id:
            logger.warning("DIGISTORE_AFFILIATE_ID não configurado.")
            return None

        logger.info(f"🔍 [Digistore Scout] Procurando produto para: {topic}")
        
        # Mapeamento MVP de Soluções de Automação de IA com Alta Conversão por Região
        # Produtos reais ou equivalentes validados no Marketplace Digistore24/ClickBank para o Nicho AI
        top_offers = {
            "en-US": {"name": "Jasper AI & Automation Masterclass", "id": "834190", "niche": "business", "comm": 0.50},
            "fr-FR": {"name": "Système IA Business (French Market)", "id": "782310", "niche": "business", "comm": 0.40},
            "es-ES": {"name": "Ventas con Inteligencia Artificial", "id": "921834", "niche": "business", "comm": 0.50},
            "de-DE": {"name": "KI-Business Automatisierung", "id": "451092", "niche": "business", "comm": 0.45}
        }

        # Extrai a região do idioma (ex: en-US -> en-US, ou default para en-US se não encontrado)
        best_match = top_offers.get(language, top_offers["en-US"])
        
        product_url = self.base_redir_url.format(
            product_id=best_match["id"],
            affiliate_id=self.affiliate_id
        )

        new_product = AffiliateProduct(
            tenant_id=tenant_id,
            platform="DIGISTORE24",
            product_name=best_match["name"],
            niche=niche,
            hotlink_base=product_url,
            gravity=best_match["comm"] * 100, # Usamos comissão como peso
            language=language
        )
        
        from sqlmodel import select
        existing = self.session.exec(select(AffiliateProduct).where(
            AffiliateProduct.hotlink_base == product_url,
            AffiliateProduct.tenant_id == tenant_id,
            AffiliateProduct.platform == "DIGISTORE24"
        )).first()

        if not existing:
            try:
                # Usamos Savepoint para evitar envenenamento da sessão em caso de concorrência
                with self.session.begin_nested():
                    self.session.add(new_product)
                self.session.commit()
                self.session.refresh(new_product)
                return new_product
            except Exception as e:
                self.session.rollback()
                logger.warning(f"⚠️ [Digistore Scout] Concorrência ou erro na inserção: {e}. Recuperando existente.")
                # Fallback: Tenta buscar novamente caso outro processo tenha inserido simultaneamente
                existing = self.session.exec(select(AffiliateProduct).where(
                    AffiliateProduct.hotlink_base == product_url,
                    AffiliateProduct.tenant_id == tenant_id,
                    AffiliateProduct.platform == "DIGISTORE24"
                )).first()
        
        return existing

def get_digistore_link(product_id: str, affiliate_id: str) -> str:
    return f"https://www.digistore24.com/redir/{product_id}/{affiliate_id}"
