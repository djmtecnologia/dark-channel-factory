import os
import requests
import json
from datetime import datetime, timedelta

class ViralAdaptiveAgent:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://www.googleapis.com/youtube/v3"

    def check_channel_performance(self, channel_id):
        """Analisa os videos recentes para detectar virais."""
        print(f"📊 [ADAPTIVE] Analisando performance do canal: {channel_id}")
        
        # 1. Busca os últimos 10 videos
        url = f"{self.base_url}/search"
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "maxResults": 10,
            "order": "date",
            "type": "video",
            "key": self.api_key
        }
        
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items: return []
            
            # BUG FIX: Some items may be Channels/Playlists if type filter fails
            v_ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
            if not v_ids: return []
            
            # 2. Pega as estatisticas reais
            stats_url = f"{self.base_url}/videos"
            stats_params = {
                "part": "statistics",
                "id": ",".join(v_ids),
                "key": self.api_key
            }
            stats_r = requests.get(stats_url, params=stats_params, timeout=15)
            stats_r.raise_for_status()
            stats_items = stats_r.json().get("items", [])
            
            # 3. Calcula a media e detecta anomalias (virais)
            # BUG FIX: viewCount can be missing or '0' as empty string on brand new videos
            views = []
            for i in stats_items:
                try:
                    vc = int(i.get("statistics", {}).get("viewCount", 0) or 0)
                    views.append(vc)
                except (ValueError, TypeError):
                    views.append(0)
                    
            avg_views = sum(views) / len(views) if views else 0
            if avg_views == 0:
                return []  # Not enough data yet
            
            # Build a fast lookup dict of video_id -> title
            # BUG FIX: original next() caused StopIteration if IDs didn't match
            id_to_title = {it["id"]["videoId"]: it["snippet"]["title"] 
                           for it in items if it.get("id", {}).get("videoId")}
            
            viral_topics = []
            for i, item in enumerate(stats_items):
                try:
                    v_count = int(item.get("statistics", {}).get("viewCount", 0) or 0)
                except (ValueError, TypeError):
                    continue
                    
                if v_count > (avg_views * 2.5): # Viral detectado!
                    v_id = item["id"]
                    topic = id_to_title.get(v_id, item.get("snippet", {}).get("title", "Unknown Topic"))
                    viral_topics.append({
                        "topic": topic,
                        "performance": v_count / avg_views,
                        "views": v_count
                    })
            
            return viral_topics
        except requests.RequestException as e:
            print(f"❌ [ADAPTIVE ERR] Erro de rede: {e}")
            return []
        except Exception as e:
            print(f"❌ [ADAPTIVE ERR] Erro ao checar performance: {e}")
            return []

    def get_production_plan(self, viral_topics):
        """Define quantos videos produzir: 3 (normal) ou 6 (viral)."""
        plan = {"target_volume": 3, "priority_topics": []}
        if viral_topics:
            print(f"🔥 [ADAPTIVE] Viral detectado! Aumentando volume para 6 videos.")
            plan["target_volume"] = 6
            plan["priority_topics"] = [t["topic"] for t in viral_topics]
        return plan

if __name__ == "__main__":
    # Teste para um canal (Ex: US)
    agent = ViralAdaptiveAgent(os.getenv("YOUTUBE_API_KEY"))
    # channel_id = "..."
    # virais = agent.check_channel_performance(channel_id)
    # print(agent.get_production_plan(virais))
