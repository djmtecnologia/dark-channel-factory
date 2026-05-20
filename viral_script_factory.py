import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

class ViralScriptFactory:
    def __init__(self):
        self.api_key = MISTRAL_API_KEY
        self.model = "mistral-large-latest"

    def generate_financial_ai_script(self, viral_brief, channel_config):
        """Gera um roteiro de alta autoridade financeira localizado para a regiao."""
        region = channel_config["region"]
        tone = channel_config["tone"]
        keywords = ", ".join(channel_config["keywords"])
        
        print(f"✍️ [FACTORY] Gerando roteiro FINANCEIRO ({region}) para: {viral_brief['target_topic']}")
        
        prompt = f"""
        Create a 10-12 minute YouTube video script about: {viral_brief['target_topic']}.
        Target Region: {region} (Tone: {tone}).
        Local Keywords to include: {keywords}.
        
        Strategy: Financial Utilitarianism. Show how to make/save money using AI.
        Localization Requirements:
        - If US: Focus on ROI, Stock Market, and aggressive wealth building.
        - If Germany: Focus on privacy, AI Act, efficiency, and engineering (SAP/ASML).
        - If Spain: Focus on side income and democratizing access to investment.
        - If France: Focus on economic innovation and local tech impact.
        
        Structure:
        1. Alpha Hook (0:00-1:30): Deep financial insight or shocking data visualization.
        2. The Strategy: Detailed breakdown of the AI tools/methods.
        3. Real Data Analysis: Mark points where charts should appear [INSERT_CHART_HERE].
        4. Regional Context: Explain why this matters for the {region} economy.
        5. Conclusion: High-ticket CTA.
        
        Requirements:
        - Output ONLY as JSON: {"title": "...", "description": "...", "tags": [...], "scenes": [{"id": 1, "text": "...", "b_roll_visual": "...", "sfx_cue": "..."}]}
        - ABSOLUTELY CRITICAL: The script MUST be at least 2500 words long to fill a 12-minute video. Generate at least 50 detailed scenes with long, engaging narration text in each. Do not summarize. Write the full spoken script.
        """
        
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = requests.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 8000
            }, timeout=180)
            
            r.raise_for_status()
            script_data = r.json()['choices'][0]['message']['content']
            return json.loads(script_data)
        except Exception as e:
            print(f"❌ [FACTORY ERR] Falha ao gerar roteiro: {e}")
            return None

if __name__ == "__main__":
    # Teste rapido para o canal dos EUA
    with open("viral_briefing.json", "r", encoding="utf-8") as f:
        brief = json.load(f)
    
    config_us = {
        "region": "US", 
        "tone": "optimistic",
        "keywords": ["ROI", "NVIDIA", "401k", "Stock Market"]
    }
    
    factory = ViralScriptFactory()
    full_script = factory.generate_financial_ai_script(brief, config_us)
    if full_script:
        with open("full_production_script.json", "w", encoding="utf-8") as f:
            json.dump(full_script, f, indent=4, ensure_ascii=False)
