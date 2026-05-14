import os
import requests
from typing import List, Optional
from sqlmodel import Session, select
from cloud_hub.models import AffiliateProduct
from shared.llm_client import get_completion
from dotenv import load_dotenv

load_dotenv()

CLICKBANK_NICKNAME = os.getenv("CLICKBANK_AFFILIATE_ID", "youtubedk")

class ClickBankScout:
    """Agente especializado em encontrar e validar produtos do ClickBank em tempo real."""
    
    def __init__(self, session: Session):
        self.session = session

    def find_and_cache_product(self, topic: str, niche: str, language: str, tenant_id) -> Optional[AffiliateProduct]:
        """
        Busca e VALIDA um produto no ClickBank baseado no tópico.
        """
        print(f"🔍 [ClickBank Scout] Procurando produto ideal para: {topic}")
        
    def find_and_cache_product(self, topic: str, niche: str, language: str, tenant_id) -> Optional[AffiliateProduct]:
        """
        Busca e VALIDA um produto no ClickBank baseado no tópico e nicho.
        """
        print(f"🔍 [ClickBank Scout] Procurando produto ideal para ({niche}): {topic}")
        
        # 1. Vendors Públicos (Auto-Approve) por Nicho - Lista Elite 2026
        niche_vendors = {
            "AI": ["aiforce", "promeai", "dfyprofis", "blasterpro", "writev", "7dfunnel"],
            "Money": ["thefasttrack", "overnightm", "fatsignals", "cryptovip"], # Removido wealthmnd
            "Business": ["dfyprofis", "blasterpro", "fatsignals", "writev"],
            "Self-Help": ["brainmask", "manifestm", "bioenergy"],
        }
        
        # Fallback e Mistura
        vendors = niche_vendors.get(niche, niche_vendors["AI"]) + niche_vendors["Business"]
        import random
        random.shuffle(vendors) # Garante que não use sempre o mesmo link
        
        valid_product = None
        for vendor_id in list(dict.fromkeys(vendors)): # Remove duplicatas mantendo ordem shuffle
            # Formato moderno e mais estável de Hoplink
            hotlink = f"https://hop.clickbank.net/?affiliate={CLICKBANK_NICKNAME}&vendor={vendor_id}"
            
            if self.validate_link(hotlink):
                print(f"✅ [ClickBank Scout] Link validado com sucesso para Vendor: {vendor_id}")
                product_name = f"Elite {niche} Solution ({vendor_id.upper()})"
                valid_product = AffiliateProduct(
                    tenant_id=tenant_id,
                    platform="CLICKBANK",
                    product_name=product_name,
                    niche=niche,
                    hotlink_base=hotlink,
                    gravity=75.0,
                    language=language
                )
                break
            else:
                print(f"⚠️ [ClickBank Scout] Vendor {vendor_id} indisponível ou link quebrado.")
            
        # 2. Salva no Cache com tratamento de duplicatas
        if not valid_product:
            return None
            
        try:
            # Verifica se já existe antes de tentar inserir
            existing = self.session.exec(select(AffiliateProduct).where(
                AffiliateProduct.hotlink_base == valid_product.hotlink_base,
                AffiliateProduct.tenant_id == tenant_id
            )).first()
            
            if existing:
                return existing

            self.session.add(valid_product)
            self.session.commit()
            self.session.refresh(valid_product)
            return valid_product
        except Exception as e:
            self.session.rollback()
            print(f"⚠️ [ClickBank Scout] Erro ao persistir produto: {e}")
            if valid_product:
                return self.session.exec(select(AffiliateProduct).where(
                    AffiliateProduct.hotlink_base == valid_product.hotlink_base
                )).first()
            return None

    def validate_link(self, url: str) -> bool:
        """Verifica se o link do ClickBank é real e não cai em página de erro."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
            }
            # ClickBank Hoplinks são 302 redirects.
            # Timeout maior para permitir carregamento lento de páginas de vendas pesadas
            res = requests.get(url, timeout=12, allow_redirects=True, headers=headers)
            
            # Se a URL final contiver padrões de erro, é inválida
            error_patterns = [
                "error-page", "invalid-hoplink", "clickbank.net/error", 
                "upsell-error", "not-found", "404", "suspended", "inactive",
                "vendoraccntstate", "errcode"
            ]
            final_url = res.url.lower()
            
            if any(indicator in final_url for indicator in error_patterns):
                return False
                
            # Se o status for 200 e não cair em erro do ClickBank, consideramos válido
            return res.status_code == 200
        except Exception as e:
            print(f"   ⚠️ [Validation DEBUG] Link check failed: {e}")
            return False

    def get_hoplink_for_video(self, product: AffiliateProduct, job_id, channel_id) -> str:
        """Personaliza o HopLink para o vídeo específico."""
        # ClickBank não aceita hífens na TID
        tid_raw = f"c{str(channel_id)[:5]}j{str(job_id)[:5]}"
        tid = tid_raw.replace("-", "")
        base = product.hotlink_base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}tid={tid}"
