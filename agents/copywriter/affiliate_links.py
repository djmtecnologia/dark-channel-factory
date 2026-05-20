"""
Affiliate Links Manager — Copywriter Agent
Templates de links de afiliado por categoria de produto.
Os URLs são placeholders — substitua com seus IDs reais nas plataformas.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

AMAZON_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20")
CLICKBANK_ID = os.getenv("CLICKBANK_AFFILIATE_ID", "your_id")


# ─────────────────────────────────────────────────────────────
# Produtos de Alta Conversão por Nicho
# Substitua URLs pelos seus links reais de afiliado
# ─────────────────────────────────────────────────────────────

AI_TOOLS_PRODUCTS = [
    {
        "platform": "direct",
        "product_name": "Jasper AI (Writing Assistant)",
        "product_description": "AI copywriting tool used by 100k+ marketers. 30-day free trial.",
        "url": "https://www.jasper.ai/?fpr=AFFILIATE_CODE",
        "commission_percent": 30.0,
        "recurring": True,
        "keywords": ["writing", "copywriting", "content", "blog", "marketing"],
    },
    {
        "platform": "direct",
        "product_name": "Notion AI",
        "product_description": "All-in-one workspace with AI built in. Replaces 5 tools.",
        "url": "https://affiliate.notion.so/AFFILIATE_CODE",
        "commission_percent": 20.0,
        "recurring": True,
        "keywords": ["productivity", "workspace", "notes", "project management"],
    },
    {
        "platform": "direct",
        "product_name": "Make.com (Automation)",
        "product_description": "Visual automation platform. Create workflows without code.",
        "url": "https://www.make.com/en/register?pc=AFFILIATE_CODE",
        "commission_percent": 20.0,
        "recurring": True,
        "keywords": ["automation", "workflow", "zapier alternative", "no-code"],
    },
    {
        "platform": "direct",
        "product_name": "Surfer SEO",
        "product_description": "AI-powered SEO tool. Rank on Google faster.",
        "url": "https://surferseo.com/?via=AFFILIATE_CODE",
        "commission_percent": 25.0,
        "recurring": True,
        "keywords": ["seo", "content optimization", "rank", "google"],
    },
    {
        "platform": "amazon",
        "product_name": "AI Superpowers Book",
        "product_description": "Best-selling book on using AI for business growth.",
        "url": f"https://amzn.to/BOOK_LINK?tag={AMAZON_TAG}",
        "commission_percent": 4.5,
        "recurring": False,
        "keywords": ["learn", "book", "education", "reading"],
    },
    {
        "platform": "direct",
        "product_name": "Descript (Video Editing AI)",
        "product_description": "Edit videos like a doc. AI removes filler words automatically.",
        "url": "https://www.descript.com/?lmref=AFFILIATE_CODE",
        "commission_percent": 15.0,
        "recurring": True,
        "keywords": ["video editing", "podcast", "youtube", "ai video"],
    },
    {
        "platform": "direct",
        "product_name": "ElevenLabs (AI Voice)",
        "product_description": "Most realistic AI voices. Clone your voice in minutes.",
        "url": "https://try.elevenlabs.io/AFFILIATE_CODE",
        "commission_percent": 22.0,
        "recurring": True,
        "keywords": ["voice", "tts", "audio", "podcast", "narration"],
    },
    {
        "platform": "clickbank",
        "product_name": "AI Pro Tool (Ultimate Engine)",
        "product_description": "Validated High-Converting AI Strategy tool.",
        "url": "https://f10b5twm8kg1tb30zalgzico7h.hop.clickbank.net/?&traffic_source=youtube&campaign=canalDarkIABusiness",
        "commission_percent": 75.0,
        "recurring": True,
        "keywords": ["ai", "trading", "money", "passive income", "engine"],
    },
    {
        "platform": "clickbank",
        "product_name": "Quantum Edge AI System",
        "product_description": "Advanced neural network for market predictions.",
        "url": "https://136a6tqf-kq9ne09oj8grjqc5x.hop.clickbank.net/?&traffic_source=youtube&campaign=canalDarkIABusiness",
        "commission_percent": 60.0,
        "recurring": True,
        "keywords": ["ai", "crypto", "forex", "trading", "bot"],
    },
    {
        "platform": "amazon",
        "product_name": "Hands-On Machine Learning (Reference)",
        "product_description": "The definitive guide to ML and Deep Learning.",
        "url": "https://www.amazon.com/dp/1492041130/?tag=canaldarkdjm-20",
        "commission_percent": 4.0,
        "recurring": False,
        "keywords": ["book", "learn", "machine learning", "python"],
    },
]


def get_relevant_products(keywords: list[str], max_products: int = 3) -> list[dict]:
    """
    Seleciona produtos de afiliado mais relevantes com base nos keywords do vídeo.
    Prioriza: comissão recorrente > porcentagem > relevância de keywords.
    """
    scored: list[tuple[float, dict]] = []

    for product in AI_TOOLS_PRODUCTS:
        score = 0.0

        # Relevância por keywords
        product_kws = set(product.get("keywords", []))
        video_kws = set(k.lower() for k in keywords)
        overlap = len(product_kws & video_kws)
        score += overlap * 10

        # Bônus por comissão recorrente
        if product.get("recurring"):
            score += 30

        # Peso da comissão
        score += product.get("commission_percent", 0) * 0.5

        scored.append((score, product))

    # Ordenar por score descendente
    scored.sort(key=lambda x: x[0], reverse=True)

    return [p for _, p in scored[:max_products]]


def build_pinned_comment(products: list[dict], additional_resources: list[str] | None = None) -> str:
    """Gera texto do comentário fixado com links de afiliado."""
    lines = ["🔗 Resources mentioned in this video:\n"]

    for i, p in enumerate(products, 1):
        lines.append(f"✅ {p['product_name']}: {p['url']}")

    if additional_resources:
        lines.append("\n📚 Additional Resources:")
        for r in additional_resources:
            lines.append(f"• {r}")

    lines.append("\n⚠️ Some links above are affiliate links. I earn a commission at no extra cost to you.")

    return "\n".join(lines)
