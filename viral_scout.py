import os
import requests
import re
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

class ViralScout:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self.niches = [
            "AI System Architecture",
            "Autonomous AI Agents",
            "Financial Engineering 2026",
            "Web3 Arbitrage",
            "AI Money Automation",
            "SaaS Architecture 2026",
            "Algorithmic Trading IA"
        ]

    def get_viral_videos(self, niche=None, region="US", days=30):
        """Busca videos que viralizaram no periodo, alternando nichos se nenhum for passado."""
        import random
        if not niche:
            niche = random.choice(self.niches)
            
        print(f"🔎 [SCOUT] Minerando videos virais ({niche}) nos {region} nos ultimos {days} dias...")
        
        published_after = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        
        search_url = f"{self.base_url}/search"
        params = {
            "part": "snippet",
            "q": niche,
            "regionCode": region,
            "relevanceLanguage": "en",
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after,
            "maxResults": 10,
            "key": self.api_key
        }
        
        try:
            r = requests.get(search_url, params=params)
            r.raise_for_status()
            items = r.json().get("items", [])
            
            videos = []
            for item in items:
                v_id = item["id"]["videoId"]
                videos.append({
                    "id": v_id,
                    "title": item["snippet"]["title"],
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "publishedAt": item["snippet"]["publishedAt"],
                    "thumb": item["snippet"]["thumbnails"]["high"]["url"]
                })
            return videos
        except Exception as e:
            print(f"❌ [SCOUT ERR] Falha na mineracao: {e}")
            return []

    def analyze_video_strategy(self, video_id):
        """Extrai metadados e transcricao (se disponivel) para entender o Hook."""
        print(f"📊 [SCOUT] Analisando estrategia do video: {video_id}")
        
        details_url = f"{self.base_url}/videos"
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": video_id,
            "key": self.api_key
        }
        
        try:
            r = requests.get(details_url, params=params)
            r.raise_for_status()
            data = r.json().get("items", [{}])[0]
            
            stats = data.get("statistics", {})
            snippet = data.get("snippet", {})
            
            return {
                "views": stats.get("viewCount"),
                "likes": stats.get("likeCount"),
                "tags": snippet.get("tags", []),
                "description": snippet.get("description", ""),
                "category": snippet.get("categoryId")
            }
        except Exception: return {}

    def generate_viral_brief(self, viral_videos):
        """Cria um briefing para a IA gerar um video melhor que os minerados."""
        if not viral_videos: return None
        
        top_video = viral_videos[0]
        strategy = self.analyze_video_strategy(top_video["id"])
        
        brief = {
            "target_topic": top_video["title"],
            "hook_reference": top_video["url"],
            "suggested_duration": "12-15 minutes",
            "improvement_angle": "Add more deep-dive technical details and real-world results that the original missed.",
            "tags": strategy.get("tags", []),
            "viral_thumb_reference": top_video["thumb"]
        }
        
        print(f"✅ [SCOUT] Briefing viral gerado: {brief['target_topic']}")
        return brief

if __name__ == "__main__":
    scout = ViralScout()
    virais = scout.get_viral_videos(niche="AI money automation")
    if virais:
        brief = scout.generate_viral_brief(virais)
        with open("viral_briefing.json", "w", encoding="utf-8") as f:
            json.dump(brief, f, indent=4, ensure_ascii=False)
