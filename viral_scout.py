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

    def check_google_trends(self, niche, region="US"):
        """Busca tendências gerais das últimas 24/48h via RSS (à prova de bloqueios) e usa IA para a ponte."""
        print(f"📈 [Google Trends] Buscando tendências via RSS Oficial (anti-bloqueio) para adaptar ao nicho: {niche}")
        try:
            import requests
            import xml.etree.ElementTree as ET
            
            # O RSS do Google Trends usa a sigla da região direta (US, BR, FR, ES, DE)
            rss_url = f"https://trends.google.com/trending/rss?geo={region}"
            
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                r = requests.get(rss_url, headers=headers, timeout=10)
                r.raise_for_status()
                root = ET.fromstring(r.content)
                
                top_trends = []
                for item in root.findall('.//item'):
                    title = item.find('title')
                    if title is not None and title.text:
                        top_trends.append(title.text)
                
                if not top_trends:
                    raise ValueError("RSS retornou vazio")
                    
                top_trends = top_trends[:10]
            except Exception as e:
                print(f"⚠️ [RSS Trends] Falha ao ler feed RSS: {e}")
                # Fallback estático de segurança caso o Google mude a URL do RSS
                top_trends = ["AI Economy", "Future Tech 2026", "Global Crisis", "New Digital Money"]

            print(f"🔥 [TRENDS] Top Assuntos nas últimas 24h ({region}): {top_trends[:3]}...")
            
            # ── [FinOps] Bridging Programático Determinístico a Custo Zero de IA ──
            enable_ai_bridging = os.getenv("ENABLE_AI_BRIDGING", "false").lower() == "true"
            
            if not enable_ai_bridging:
                import random
                # Seleciona a melhor tendência do feed
                chosen_trend = top_trends[0] if top_trends else "New Technologies"
                
                # Coleção de templates clickbait de altíssimo impacto para YouTube (Clean & High-CTR)
                templates = [
                    "How {trend} Is Changing The Future of {niche}",
                    "Why {trend} Is The Ultimate Hack For {niche}",
                    "How {niche} Is Predicting The Rise of {trend}",
                    "The Secrets Behind {trend} and {niche} in 2026",
                    "{trend} Meets {niche}: The 2026 Business Revolution",
                    "Why {trend} Is Accelerating The Growth of {niche}",
                    "How {niche} Solves The Main Crisis of {trend}"
                ]
                
                # Conecta de forma limpa e inteligente
                template = random.choice(templates)
                bridged_topic = template.format(trend=chosen_trend, niche=niche)
                print(f"🧠 [Programmatic Bridge] Tópico Híbrido Criado a Custo Zero: '{bridged_topic}'")
                return bridged_topic
            
            # Caso contrário, tenta conectar via Inteligência Artificial
            try:
                import sys
                from shared.ai_orchestrator import AIOrchestrator
                
                prompt = f"""You are a viral YouTube content strategist for region {region}.
Channel Niche: {niche}
Top 10 trending topics right now in {region}:
{top_trends}

Task:
1. Understand the true local/cultural context of these trends (e.g. 'Racing' in Spain is a soccer team, not car racing).
2. Select 1 trending topic from the list and creatively bridge it with the channel's niche.
   RULE: You MUST explicitly include the subject/name of the chosen trend in your final concept!
   Example: If trend is 'Granada' (soccer) and niche is 'AI', the topic is 'How AI is predicting Granada matches'.
3. OUTPUT STRICTLY A YOUTUBE SEARCH QUERY in English (max 7 words). DO NOT output conversational text. DO NOT use quotes.
Example Output: AI predicting Granada soccer matches"""
                
                ai = AIOrchestrator()
                resp = ai.chat_completion(system_prompt="You are a brilliant YouTube strategist.", user_prompt=prompt)
                bridged_topic = resp.text.strip(' "\'\n')
                print(f"🧠 [IA Bridge] Tópico Híbrido Criado: '{bridged_topic}'")
                return bridged_topic
                
            except Exception as e:
                print(f"⚠️ [IA Bridge] Falha de conexao com a IA: {e}")
                return top_trends[0] + " " + niche
                
        except Exception as e:
            print(f"⚠️ [TRENDS] Erro fatal no motor de tendências: {e}")
            
        return None

    def get_viral_videos(self, niche=None, region="US", days=7):
        """Busca videos que viralizaram baseados no Google Trends ou no nicho."""
        import random
        if not niche:
            niche = random.choice(self.niches)
            
        # 1. Verifica no Google Trends se há um sub-tópico fervendo
        hot_topic = self.check_google_trends(niche, region)
        search_query = hot_topic if hot_topic else niche
            
        print(f"🔎 [SCOUT] Minerando no YouTube: '{search_query}' (Região: {region}, Últimos {days} dias)...")
        
        published_after = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        
        search_url = f"{self.base_url}/search"
        params = {
            "part": "snippet",
            "q": search_query,
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
            
            # Se a busca no YouTube retornou vazia mas temos um topico quente do Google Trends, usamos ele!
            if not items and hot_topic:
                print(f"💡 [SCOUT FALLBACK] Nao encontramos videos no YouTube para a query '{search_query}'. Usando topico do Google Trends diretamente.")
                return [{
                    "id": "trends_fallback",
                    "title": hot_topic,
                    "url": "",
                    "publishedAt": datetime.utcnow().isoformat(),
                    "thumb": ""
                }]
            
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
            
            # Garantir que o tópico híbrido gerado pelo Google Trends não seja descartado/ignorado
            if hot_topic and videos:
                print(f"🔥 [SCOUT] Protegendo tendência do Google Trends: Definindo título principal como '{hot_topic}'")
                videos[0]["title"] = hot_topic

            return videos
        except Exception as e:
            print(f"❌ [SCOUT ERR] Falha na mineracao: {e}")
            if hot_topic:
                print(f"💡 [SCOUT FALLBACK] Usando topico hibrido do Google Trends diretamente apos falha: '{hot_topic}'")
                return [{
                    "id": "trends_fallback",
                    "title": hot_topic,
                    "url": "",
                    "publishedAt": datetime.utcnow().isoformat(),
                    "thumb": ""
                }]
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
