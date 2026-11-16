"""
Configuração Central — Dark Channel Factory
Define os canais, parâmetros de niche e agendamento.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class ChannelConfig:
    """Configuração de um canal YouTube."""
    id: str
    name: str
    language: str
    voice: str
    niche: str
    niche_category_id: str          # YouTube category ID
    youtube_token_file: str
    amazon_affiliate_tag: str
    clickbank_affiliate_id: str
    publish_days: list[str]         # ["tuesday", "thursday", "saturday"]
    publish_time_utc: str           # "14:00"
    tts_rate: str = "+15%"
    tts_volume: str = "+10%"
    video_resolution: str = "1920x1080"
    video_fps: int = 30
    target_duration_s: int = 900    # 15 minutos (garante mínimo QC de 720s para LONG)
    ctr_threshold: float = 4.0      # % → troca thumbnail
    retention_threshold: float = 45.0  # % → dispara deep dive


# ── Canal Principal: AI Tools (EUA, en-US) ────────────────────
# Nicho escolhido: AI Tools & Business Automation
# Motivo: CPM $25-40 (B2B Software), afiliados SaaS com 30-40% recorrente,
#         demanda explosiva em 2026-2027, fácil de produzir com IA
# ──────────────────────────────────────────────────────────────
CHANNELS: list[ChannelConfig] = [
    ChannelConfig(
        id="ai_tools_us",
        name="AIToolsUnlocked",           # Nome sugerido do canal
        language="en-US",
        voice="en-US-AriaNeural",          # Voz neural feminina (alta retenção)
        niche="ai_tools",
        niche_category_id="28",            # Science & Technology
        youtube_token_file="credentials/youtube_token.json",
        amazon_affiliate_tag=os.getenv("AMAZON_AFFILIATE_TAG", "yourtag-20"),
        clickbank_affiliate_id=os.getenv("CLICKBANK_AFFILIATE_ID", "your_id"),
        publish_days=os.getenv("PUBLISH_DAYS", "tuesday,thursday,saturday").split(","),
        publish_time_utc=os.getenv("PUBLISH_TIME_UTC", "14:00"),
    ),
    ChannelConfig(
        id="ai_tools_de",
        name="KIWerkzeugeDE",
        language="de-DE",
        voice="de-DE-KillianNeural",
        niche="ai_tools",
        niche_category_id="28",
        youtube_token_file="credentials/youtube_token_de.json",
        amazon_affiliate_tag=os.getenv("AMAZON_AFFILIATE_TAG_DE", "yourtag-de-21"),
        clickbank_affiliate_id=os.getenv("CLICKBANK_AFFILIATE_ID_DE", "your_id"),
        publish_days=["monday", "wednesday", "friday"],
        publish_time_utc="17:00",
    ),
    ChannelConfig(
        id="ai_tools_es",
        name="HerramientasIAES",
        language="es-ES",
        voice="es-ES-AlvaroNeural",
        niche="ai_tools",
        niche_category_id="28",
        youtube_token_file="credentials/youtube_token_es.json",
        amazon_affiliate_tag=os.getenv("AMAZON_AFFILIATE_TAG_ES", "yourtag-es-21"),
        clickbank_affiliate_id=os.getenv("CLICKBANK_AFFILIATE_ID_ES", "your_id"),
        publish_days=["monday", "wednesday", "friday"],
        publish_time_utc="17:00",
    ),
    ChannelConfig(
        id="ai_tools_fr",
        name="OutilsIAFrance",
        language="fr-FR",
        voice="fr-FR-HenriNeural",
        niche="ai_tools",
        niche_category_id="28",
        youtube_token_file="credentials/youtube_token_fr.json",
        amazon_affiliate_tag=os.getenv("AMAZON_AFFILIATE_TAG_FR", "yourtag-fr-21"),
        clickbank_affiliate_id=os.getenv("CLICKBANK_AFFILIATE_ID_FR", "your_id"),
        publish_days=["monday", "wednesday", "friday"],
        publish_time_utc="17:00",
    ),
    # Descomente e configure para adicionar mais canais em outros idiomas:
    # ChannelConfig(
    #     id="ai_tools_br",
    #     name="AIToolsBrasil",
    #     language="pt-BR",
    #     voice="pt-BR-FranciscaNeural",
    #     niche="ai_tools",
    #     niche_category_id="28",
    #     youtube_token_file="credentials/youtube_token_br.json",
    #     amazon_affiliate_tag="yourtag-br-20",
    #     clickbank_affiliate_id="your_id",
    #     publish_days=["segunda", "quarta", "sexta"],
    #     publish_time_utc="17:00",
    # ),
]


def get_channel(channel_id: str) -> ChannelConfig | None:
    for ch in CHANNELS:
        if ch.id == channel_id:
            return ch
    return None


def get_default_channel() -> ChannelConfig:
    return CHANNELS[0]
