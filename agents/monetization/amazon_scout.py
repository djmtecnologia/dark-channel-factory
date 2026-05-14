import os
from typing import Optional
from sqlmodel import Session, select
from cloud_hub.models import AffiliateProduct
from shared.llm_client import get_completion
from dotenv import load_dotenv

load_dotenv()

AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "canaldarkdjm-20")

import json

CURATED_AMAZON_CATALOG = [
    {
        "asin": "1098121228",
        "name": "Fundamentals of Software Architecture / AI Engineering",
        "keywords": ["software engineering", "coding", "programming", "AI replacing coders", "tech careers"]
    },
    {
        "asin": "1492041130",
        "name": "Data Science from Scratch: First Principles with Python",
        "keywords": ["data science", "python", "machine learning", "AI", "artificial intelligence"]
    },
    {
        "asin": "0593459660",
        "name": "Atomic Habits: An Easy & Proven Way to Build Good Habits & Break Bad Ones",
        "keywords": ["habits", "success", "productivity", "mindset", "business", "motivation"]
    },
    {
        "asin": "1942788339",
        "name": "The Millionaire Fastlane",
        "keywords": ["finance", "money", "business", "wealth", "financial freedom", "side hustle"]
    },
    {
        "asin": "B00N1YPXW2",
        "name": "Blue Yeti USB Microphone",
        "keywords": ["youtube setup", "microphone", "streaming", "content creation", "hardware", "faceless channel"]
    },
    {
        "asin": "B09G96TFF7",
        "name": "Logitech MX Master 3S Wireless Mouse",
        "keywords": ["setup", "productivity", "hardware", "developer tools", "office setup"]
    },
    {
        "asin": "1119656114",
        "name": "The Psychology of Money",
        "keywords": ["finance", "investing", "wealth", "mindset", "money", "saving"]
    },
    {
        "asin": "0593157826",
        "name": "Deep Work: Rules for Focused Success in a Distracted World",
        "keywords": ["productivity", "focus", "success", "work", "career"]
    },
    {
        "asin": "B089WG66L2",
        "name": "Sony WH-1000XM4 Noise Canceling Headphones",
        "keywords": ["focus", "noise cancelling", "hardware", "developer tools", "remote work"]
    },
    {
        "asin": "1491962291",
        "name": "Hands-On Machine Learning with Scikit-Learn, Keras, and TensorFlow",
        "keywords": ["machine learning", "ai", "tensorflow", "neural networks", "data science"]
    },
    {
        "asin": "B08Z9M6KH9",
        "name": "Apple 2021 MacBook Pro (14-inch, M1 Pro chip)",
        "keywords": ["hardware", "developer laptop", "workstation", "tech", "laptop"]
    },
    {
        "asin": "0143111288",
        "name": "The 4-Hour Workweek",
        "keywords": ["lifestyle design", "automation", "outsourcing", "business", "freedom"]
    },
    {
        "asin": "B00X4WHP5E",
        "name": "Kindle Paperwhite",
        "keywords": ["reading", "education", "books", "tech gadgets", "learning"]
    }
]

class AmazonScout:
    """Agente especializado em encontrar e recomendar produtos na Amazon."""
    
    def __init__(self, session: Session):
        self.session = session

    def find_and_cache_product(self, topic: str, niche: str, language: str, tenant_id) -> Optional[AffiliateProduct]:
        """
        Busca o ASIN mais relevante no catálogo curado usando IA para garantir links diretos válidos.
        """
        print(f"📦 [Amazon Scout] Procurando link direto ideal para o tópico: {topic}")
        
        catalog_json = json.dumps([{"asin": c["asin"], "name": c["name"], "keywords": c["keywords"]} for c in CURATED_AMAZON_CATALOG])
        
        # 1. Escolha do Produto via IA
        prompt = f"""
        Analyze the video topic: '{topic}'
        Below is a curated catalog of valid Amazon products.
        Select the SINGLE best product that perfectly relates to the topic.
        If nothing fits perfectly, pick the most broadly useful one (e.g., Atomic Habits).
        
        Catalog:
        {catalog_json}
        
        Return ONLY the raw ASIN string (e.g., 1098121228). Do not write anything else.
        """
        selected_asin = get_completion(prompt, system_instruction="You are an Amazon Associate conversion expert. Output only the ASIN.").strip()
        
        # Limpa qualquer aspas
        selected_asin = selected_asin.replace('"', '').replace("'", "").strip()
        
        # Encontra os detalhes do produto no catálogo
        matched_product = next((p for p in CURATED_AMAZON_CATALOG if p["asin"] == selected_asin), CURATED_AMAZON_CATALOG[0])
        
        # Tradução do nome do produto para o idioma local
        translated_name = matched_product["name"]
        if language != "en-US":
            try:
                trans_prompt = f"Translate this product name to {language}: '{matched_product['name']}'. Return ONLY the translated name."
                translated_name = get_completion(trans_prompt, system_instruction="You are a professional translator. Output only the translation.").strip()
            except: pass
        
        # 2. Gera o Link Direto com a Tag e TLD Regional
        tld_map = {
            "fr-FR": "fr",
            "es-ES": "es",
            "de-DE": "de",
            "pt-BR": "com.br"
        }
        tld = tld_map.get(language, "com")
        hotlink = f"https://www.amazon.{tld}/dp/{matched_product['asin']}/?tag={AMAZON_AFFILIATE_TAG}"
        
        # 3. Salva no Cache
        new_product = AffiliateProduct(
            tenant_id=tenant_id,
            platform="AMAZON",
            product_name=translated_name,
            niche=niche,
            hotlink_base=hotlink,
            gravity=95.0, # Amazon Links diretos convertem muito bem
            language=language
        )
        
        existing = self.session.exec(select(AffiliateProduct).where(
            AffiliateProduct.hotlink_base == hotlink,
            AffiliateProduct.tenant_id == tenant_id,
            AffiliateProduct.platform == "AMAZON"
        )).first()
        
        if not existing:
            try:
                with self.session.begin_nested():
                    self.session.add(new_product)
                self.session.commit()
                self.session.refresh(new_product)
                return new_product
            except Exception as e:
                self.session.rollback()
                print(f"⚠️ [Amazon Scout] Concorrência detectada: {e}. Recuperando existente...")
                existing = self.session.exec(select(AffiliateProduct).where(
                    AffiliateProduct.hotlink_base == hotlink,
                    AffiliateProduct.tenant_id == tenant_id,
                    AffiliateProduct.platform == "AMAZON"
                )).first()
        
        return existing
