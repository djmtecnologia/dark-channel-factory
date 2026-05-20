"""
YouTube Trends & Competition Analysis — Scout & Miner Agent
Usa pytrends + YouTube Data API v3 para minerar oportunidades.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Tópicos seed para o nicho AI Tools
AI_TOOLS_SEED_KEYWORDS = [
    "AI tools for business",
    "ChatGPT automation",
    "AI productivity tools 2026",
    "best AI tools",
    "artificial intelligence business automation",
    "AI marketing tools",
    "AI content creation",
    "make money with AI",
    "AI tools entrepreneurs",
    "automate business with AI",
]


class TrendsAnalyzer:
    """Analisa trending topics via Google Trends (pytrends)."""

    def __init__(self) -> None:
        self._hl = "en-US"
        self._geo = "US"

    def get_rising_queries(self, keywords: list[str]) -> dict[str, Any]:
        """Retorna queries em alta e hot topics (presentes em 7d e 24h)."""
        try:
            from pytrends.request import TrendReq

            pt = TrendReq(hl=self._hl, tz=0, timeout=(10, 25))

            results: dict[str, list] = {"rising": [], "top": [], "hot_topics": []}

            for kw in keywords[:2]:  # Limitado a 2 para evitar rate limit
                try:
                    # Busca de 7 dias
                    pt.build_payload([kw], timeframe="now 7-d", geo=self._geo)
                    rising_7d = []
                    r7 = pt.related_queries().get(kw, {}).get("rising")
                    if r7 is not None and not r7.empty:
                        rising_7d = r7.head(10)["query"].tolist()
                        results["rising"].extend(rising_7d[:5])
                    
                    time.sleep(2)
                    
                    # Busca de 24 horas
                    pt.build_payload([kw], timeframe="now 1-d", geo=self._geo)
                    rising_1d = []
                    r1 = pt.related_queries().get(kw, {}).get("rising")
                    if r1 is not None and not r1.empty:
                        rising_1d = r1.head(10)["query"].tolist()
                        results["rising"].extend(rising_1d[:5])
                    
                    # Interseção (Assuntos quentes em ambos os períodos)
                    hot = list(set(rising_7d) & set(rising_1d))
                    if hot:
                        results["hot_topics"].extend(hot)
                    
                    time.sleep(2)
                except Exception as e:
                    logger.warning(f"pytrends error for '{kw}': {e}")
                    continue

            results["rising"] = list(dict.fromkeys(results["rising"]))
            results["hot_topics"] = list(dict.fromkeys(results["hot_topics"]))
            return results

        except ImportError:
            logger.warning("pytrends not installed — returning seed keywords")
            return {"rising": AI_TOOLS_SEED_KEYWORDS[:5], "top": AI_TOOLS_SEED_KEYWORDS[5:], "hot_topics": []}
        except Exception as e:
            logger.error(f"Trends analysis failed: {e}")
            return {"rising": AI_TOOLS_SEED_KEYWORDS[:5], "top": [], "hot_topics": []}


class YouTubeCompetitionAnalyzer:
    """Analisa competição no YouTube via Data API v3."""

    def __init__(self, api_key: str = YOUTUBE_API_KEY) -> None:
        self.api_key = api_key
        self.session = requests.Session()

    def _search(self, query: str, max_results: int = 10) -> list[dict]:
        """Busca vídeos no YouTube e retorna resultados."""
        if not self.api_key or self.api_key == "your_youtube_data_api_key_here":
            logger.warning("YouTube API key not set — returning mock competition data")
            return self._mock_results(query)

        try:
            params = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": max_results,
                "order": "viewCount",
                "regionCode": "US",
                "relevanceLanguage": "en",
                "videoDuration": "medium",  # 4-20 minutos
                "key": self.api_key,
            }
            response = self.session.get(f"{YOUTUBE_API_BASE}/search", params=params, timeout=15)
            response.raise_for_status()
            items = response.json().get("items", [])
            return items
        except Exception as e:
            logger.error(f"YouTube search failed: {e}")
            return self._mock_results(query)

    def get_video_stats(self, video_ids: list[str]) -> dict[str, dict]:
        """Retorna estatísticas de múltiplos vídeos."""
        if not self.api_key or self.api_key == "your_youtube_data_api_key_here":
            return {}

        try:
            params = {
                "part": "statistics,contentDetails",
                "id": ",".join(video_ids[:50]),
                "key": self.api_key,
            }
            response = self.session.get(f"{YOUTUBE_API_BASE}/videos", params=params, timeout=15)
            response.raise_for_status()
            items = response.json().get("items", [])
            return {item["id"]: item for item in items}
        except Exception as e:
            logger.error(f"YouTube stats fetch failed: {e}")
            return {}

    def analyze_competition(self, keyword: str) -> dict[str, Any]:
        """Analisa concorrência para um keyword e retorna insights."""
        results = self._search(keyword, max_results=10)
        if not results:
            return {"keyword": keyword, "competition": "unknown", "insights": []}

        video_ids = [
            r.get("id", {}).get("videoId", "")
            for r in results
            if isinstance(r.get("id"), dict)
        ]
        stats = self.get_video_stats(video_ids)

        total_views = 0
        view_counts = []
        for vid_id, data in stats.items():
            views = int(data.get("statistics", {}).get("viewCount", 0))
            total_views += views
            view_counts.append(views)

        avg_views = total_views / len(view_counts) if view_counts else 0
        max_views = max(view_counts) if view_counts else 0

        competition_level = "low"
        if max_views > 1_000_000:
            competition_level = "high"
        elif max_views > 100_000:
            competition_level = "medium"

        # Identifica títulos dos top vídeos para encontrar gaps
        top_titles = [
            r.get("snippet", {}).get("title", "")
            for r in results[:5]
        ]

        return {
            "keyword": keyword,
            "competition": competition_level,
            "avg_views": int(avg_views),
            "max_views": max_views,
            "top_titles": top_titles,
            "result_count": len(results),
            "gap_signals": self._identify_gaps(top_titles),
        }

    def _identify_gaps(self, titles: list[str]) -> list[str]:
        """Identifica gaps nos títulos dos concorrentes."""
        gaps = []
        combined = " ".join(titles).lower()

        checks = [
            ("roi" not in combined and "results" not in combined, "No ROI/real results shown"),
            ("2026" not in combined and "2027" not in combined, "Outdated content (no 2026/2027)"),
            ("free" not in combined, "No free tools mentioned"),
            ("beginner" not in combined and "step by step" not in combined, "No beginner-friendly approach"),
            ("workflow" not in combined and "automation" not in combined, "No workflow demonstration"),
        ]

        for condition, gap in checks:
            if condition:
                gaps.append(gap)

        return gaps

    def _mock_results(self, query: str) -> list[dict]:
        """Dados mock quando a API key não está configurada."""
        return [
            {
                "id": {"videoId": f"mock_{i}"},
                "snippet": {
                    "title": f"Top AI Tools for Business {i+1} | 2024 Guide",
                    "description": "Mock description",
                    "publishedAt": "2024-01-01T00:00:00Z",
                }
            }
            for i in range(5)
        ]


def collect_trend_data(language: str = "en-US") -> dict[str, Any]:
    """
    Coleta dados de tendências e competição para o nicho AI Tools.
    Retorna dict formatado para o LLM Scout Agent.
    """
    logger.info("🔍 Collecting trend & competition data...")

    trends = TrendsAnalyzer()
    competition = YouTubeCompetitionAnalyzer()

    # Coleta trending queries
    trend_data = trends.get_rising_queries(AI_TOOLS_SEED_KEYWORDS[:3])

    # Analisa competição para os top seeds
    competition_insights = []
    for kw in AI_TOOLS_SEED_KEYWORDS[:2]:
        insight = competition.analyze_competition(kw)
        competition_insights.append(insight)
        time.sleep(1)
        
    hot_topics = trend_data.get("hot_topics", [])
    modeling_targets = []
    if hot_topics:
        logger.info(f"🔥 Found Hot Topics (7d & 24h intersect): {hot_topics}")
        for hot in hot_topics[:2]: # Pega os 2 mais quentes
            # search YouTube for hot topic, ordered by viewCount
            insight = competition.analyze_competition(hot)
            if insight and insight.get("top_titles"):
                modeling_targets.append({
                    "topic": hot,
                    "target_titles": insight.get("top_titles", [])[:3], # Top 3 most viewed
                    "max_views": insight.get("max_views", 0)
                })

    return {
        "trending_queries": trend_data,
        "competition_analysis": competition_insights,
        "modeling_targets": modeling_targets,
        "seed_keywords": AI_TOOLS_SEED_KEYWORDS,
        "target_region": "US",
        "language": language,
    }
