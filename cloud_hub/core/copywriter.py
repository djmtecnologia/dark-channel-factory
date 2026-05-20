from typing import Dict, Any
from uuid import UUID
from sqlmodel import Session
from cloud_hub.models import VideoJob, Channel, Tenant
from cloud_hub.core.monetization import get_best_match_product, inject_tracking_link
from shared.llm_client import get_completion
from shared.ai_orchestrator import orchestrator
from shared.retention_engine import build_retention_prompt
from shared.script_validator import validate_and_fix_script
from datetime import datetime, timezone

def sanitize_script_glossary(script_data: dict) -> dict:
    """
    Sanitiza os textos do roteiro (narração, título, descrição) para garantir
    a consistência perfeita dos termos e marcas inventadas pela IA, bem como
    correção de anos (2016 -> 2026), remoção de repetições e conclusão de sentenças.
    """
    if not script_data:
        return script_data
    
    # 1. Dicionário de Substituições de Termos e Erros de Coesão / Tradução
    replacements = {
        # MotoGP/Formula E Teams / Tools Fictícios (Espanhol, Francês e outros)
        "Tecride": "Tekride",
        "Treuder": "Tekride",
        "Treider": "Tekride",
        "Tecrdet": "Tekride",
        "tekride": "Tekride",
        "treuder": "Tekride",
        "treider": "Tekride",
        "tecrdet": "Tekride",
        
        # Race 26 Token Normalizações
        "Ris 26": "Race26",
        "Raza 26": "Race26",
        "Raça 26": "Race26",
        "Ascensión 26": "Race26",
        "Ascension 26": "Race26",
        "Race 26": "Race26",
        "race 26": "Race26",
        
        # Plataformas Fictícias
        "Raselinkai": "RaceLinkAI",
        "RaselinkAI": "RaceLinkAI",
        "Arrael Inkai": "RaceLinkAI",
        "Arrael InkAI": "RaceLinkAI",
        "Race Link AI": "RaceLinkAI",
        "Array Link AI": "RaceLinkAI",
        
        # Correções de Termos Vulgares/Pouco Profissionais
        "putao": "puntero",
        "ritmo putao": "ritmo de punta",
        "pentante punter": "puntero",
        
        # Algoritmos / Ferramentas de dados Fictícias
        "Rasrix": "RaceMetrics",
        "Racemetric": "RaceMetrics",
        "Racemetrics": "RaceMetrics",
        "racemetrics": "RaceMetrics",
        "racemetric": "RaceMetrics",
        
        # Algoritmos Fictícios (França e outros)
        "Rasspredict": "RacePredict",
        "Rass Prédict": "RacePredict",
        "Rass Predict": "RacePredict",
        "Retrict": "RacePredict",
        "Race Predict": "RacePredict",
        "rasspredict": "RacePredict",
        "retrict": "RacePredict",
        "race predict": "RacePredict",
        
        # Outras inconsistências
        "ridata s": "Ridata",
        "ridatas": "Ridata",
        "ridata's": "Ridata",
        
        # Verificação do Ritmo Visual e Estrutural (Meio do vídeo / Tutorial)
        "en el mini tutorial de la mitad del vídeo": "a continuación en nuestro mini tutorial",
        "en el mini tutorial de la mitad del video": "a continuación en nuestro mini tutorial",
        "en el tutorial de la mitad del vídeo": "a continuación en nuestro tutorial",
        "en el tutorial de la mitad del video": "a continuación en nuestro tutorial",
        
        # Consistência lógica na mecânica esportiva (Fórmula E - Attack Mode)
        "cambio de neumáticos": "activación del Modo Ataque",
        "cambio de neumaticos": "activación del Modo Ataque",
        "entrar a boxes para cambiar neumáticos": "activar el Modo Ataque",
        "entrar a boxes para cambiar neumaticos": "activar el Modo Ataque",
        "entrar a boxes para una troca de pneus": "activar o Modo Ataque",
        "cambiar neumáticos": "activar el Modo Ataque",
        "cambiar neumaticos": "activar el Modo Ataque",
        "cambiar los neumáticos": "activar el Modo Ataque",
        "cambiar os pneus": "activar o Modo Ataque",
        "troca de pneus": "ajustes de gerenciamento de energia",
        "parada en boxes": "activación de Modo Ataque",
        "paradas en boxes": "activaciones de Modo Ataque",
        
        # Correção de Perguntas Redundantes / Tag de Pergunta "no" em espanhol
        "con éxito no Si": "con éxito? Si",
        "con exito no Si": "con éxito? Si",
        "con éxito, no? Si": "con éxito? Si",
        "con exito, no? Si": "con éxito? Si",
        "éxito no Si": "éxito? Si",
        "exito no Si": "éxito? Si",
        ", no? Si": "? Si",
        "? no. Si": "? Si",
        "? no Si": "? Si",
        
        # Correção Gramatical de Truncamento do Roteiro (Exemplos específicos e gerais)
        "pour Mais voici": "pour cela. Mais voici",
        "pour Mais": "pour isso. Mais",
        "pour les contrats intelligents Mais": "pour les contrats intelligents. Mais",
        
        # Repetições de Valores do áudio (Pausas Narrativas Dramáticas)
        "28 millions d'euros 28 millions": "28 millions d'euros. Je répète, 28 millions",
        "28 millions d'euros 28 millions d'euros": "28 millions d'euros",
        
        # Correção Temporal de data (2016 -> 2026)
        "dès 2016": "dès 2026",
        "depuis 2016": "depuis 2026",
        "en 2016": "en 2026",
        "de 2016": "de 2026",
    }
    
    def clean_text(text: str) -> str:
        if not isinstance(text, str):
            return text
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    # Higieniza campos principais
    if "title" in script_data and script_data["title"]:
        script_data["title"] = clean_text(script_data["title"])
    if "description" in script_data and script_data["description"]:
        script_data["description"] = clean_text(script_data["description"])
    if "script_full" in script_data and script_data["script_full"]:
        script_data["script_full"] = clean_text(script_data["script_full"])
        
    # Higieniza cenas
    scenes = script_data.get("scenes", [])
    if isinstance(scenes, list):
        for s in scenes:
            if isinstance(s, dict):
                for k in ["narration", "text", "texto", "content"]:
                    if k in s and s[k]:
                        s[k] = clean_text(s[k])
                        
    return script_data

# Legacy system prompt kept for reference — replaced by shared.retention_engine.ELITE_SYSTEM_PROMPT
SYSTEM_PROMPT = """
You are a Senior Viral Video Director and Retention Engineering Architect (LEVEL 100/100).
Your goal is to transform \"AI Slop\" into Premium, Cinematic, High-Authority Business Documentaries.

CORE DIRECTIVES:
1. THE 0-5s RULE: Every video must start with a curiosity explosion. Shocking statements, extreme results, or conflict. NO intros.
2. DOPAMINE PACING: Pattern interrupts every 5-10 seconds. Use visual zooms, motion graphics, and emotional sound transitions.
3. STORYTELLING AS DISCOVERY: Frame content as a business experiment, high-stakes test, or futuristic discovery.
4. ELIMINATE AI-GENERICISM: No robotic pacing, no overused stock footage, no predictable editing.
5. CONTEXT 2026: The algorithm rewards human-directed, emotionally engineered content.
"""

MASTER_SCRIPT_PROMPT = """
# 🎬 🧠 MASTER PROMPT — VIRAL BUSINESS CINEMATIC

## 🌍 TARGET LANGUAGE: {target_language}
## 🎯 CORE TOPIC / TREND TO WRITE ABOUT: {topic}

## 🔤 GLOSSARY & COHESION REQUIREMENTS (MANDATORY):
- Keep brand names, fictitious tools, and algorithms 100% consistent across all scenes. 
- Fictional MotoGP team name MUST always be spelled "Tekride".
- Fictional predictive algorithm MUST always be spelled "RacePredict".
- Ensure that sentences NEVER end abruptly or contain grammatical truncations (e.g. do not end a scene narration in the middle of a clause).
- If referring to the current year or predictions, ALWAYS use the year 2026. Do not mention 2016.

## 📊 RETENTION & DURATION INSTRUCTIONS:
{duration_instructions}

## 🎙️ NARRATION PACING INSTRUCTIONS:
Each scene must contain {narration_instructions}

## 💡 SUCCESS PATTERNS & SYSTEM FEEDBACK:
{optimization_notes}

## 💰 AFFILIATE LINK TO PROMOTE:
{affiliate_url}

You are writing for a top-tier faceless AI business channel. 
The goal is HIGH-CTR, HIGH-RETENTION, and ALGORITHMIC OBSESSION.

## 🎯 VIDEO OBJECTIVES
- **HOOK (0-5s):** Start with a shock in {target_language}.
- **RETENTION:** Create curiosity loops. Every scene must lead to the next "payoff".
- **STORYTELLING:** Follow a structure of: 1. Curiosity -> 2. Tension -> 3. Escalation -> 4. Reveal -> 5. Next Loop.
- **VISUALS (CRITICAL):** b_roll_visual must ALWAYS be in ENGLISH for search compatibility. The visual query MUST exactly match and represent the narrated script. If the script talks about a specific object, interface, or action, the visual query must ask for that explicitly.

## 🚀 OUTPUT REQUIREMENTS:
1. **ALL TEXT FIELDS (narration, description, title, marketing_strategy, quiz, thumb_text_vars) MUST BE WRITTEN IN {target_language}.**
2. **ALL B_ROLL_VISUAL FIELDS MUST BE WRITTEN IN ENGLISH AND HIGHLY SPECIFIC TO THE SCENE'S NARRATION.**
3. Viral title options (3-5 high-CTR options)
4. High-CTR thumbnail concepts (3 visual descriptions)
5. Hook variations (3 different ways to start the video)
6. Retention pacing map (Time-based breakdown of pattern interrupts)
7. Scene-by-scene structure (JSON detailed below)
8. Emotional pacing system (Mapping the viewer's emotional state)
9. Sound design recommendations (Risers, impacts, bass hits)
10. Visual transition instructions (Zooms, wipes, glitches)
11. Curiosity loop placements (Where the "open loops" are created)
12. Shorts extraction strategy (3 timestamps for viral shorts)
13. Thumbnail text variations (1-4 words maximum, bold)
14. Thumbnail composition instructions (Focal point, contrast)
15. CTR optimization notes (Psychology of the click)
16. Retention optimization notes (How to keep them watching)
17. Binge-watch connection strategy (How to lead to the next video)

## 🎨 OUTPUT FORMAT (JSON ONLY):
{{
    "marketing_strategy": {{
        "viral_titles": ["Titulos em {target_language}"],
        "thumb_concepts": ["Concepts in {target_language}"],
        "hook_vars": ["Hooks in {target_language}"],
        "pacing_map": "",
        "emotional_pacing": "",
        "sound_design": "",
        "visual_instructions": "",
        "curiosity_loops": "",
        "shorts_strategy": "",
        "thumb_text_vars": [],
        "thumb_composition": "",
        "ctr_notes": "",
        "retention_notes": "",
        "binge_strategy": ""
    }},
    "title": "PRIMARY TITLE IN {target_language}",
    "description": "DESCRIPTION IN {target_language}",
    "tags": ["tags in {target_language}"],
    "music_mood": "cinematic|epic|suspenseful",
    "scenes": [
        {{
            "timestamp": "0:00-0:05",
            "narration": "SHOCKING HOOK IN {target_language} (MANDATORY: AT LEAST 100 WORDS PER SCENE FOR LONG VIDEOS)",
            "b_roll_visual": "DETAILED CINEMATIC VISUAL QUERY IN ENGLISH (ALWAYS ENGLISH)",
            "sfx_cue": "impact|riser|whoosh"
        }},
        ...
    ],
    "quiz": {{
        "question": "Question in {target_language}",
        "answers": ["Answers in {target_language}"],
        "correct_index": 0
    }}
}}
"""


LOCALIZATION_PROMPT = """
59. You are an expert content localizer and cultural expert. 
60. Translate and ADAPT this YouTube script for {target_language}.
61. DEEP ADAPTATION: Change US-centric references (like 'Starbucks' or 'Venmo') to local equivalent champions (like 'Pão de Açúcar' or 'Pix' in Brazil, or 'Mercado Pago' in LatAm). Use current local slang that keeps 18-34 year olds hooked.
62. Maintain the same JSON structure and timestamps.
"""

# Exemplo de como o prompt será montado no agente
def build_optimized_prompt(base_topic, optimization_notes):
    prompt = f"""
    Você é um roteirista de elite.
    TÓPICO: {base_topic}
    
    APRENDIZADO DOS VÍDEOS ANTERIORES:
    {optimization_notes}
    
    Se as notas indicam 'Hook fraco', você deve abandonar o estilo anterior 
    e modelar o novo gancho com base nos padrões virais sugeridos.
    """
    return prompt

async def run_copywriter_phase(session: Session, job: VideoJob):
    """
    Gera o roteiro do vídeo e injeta a monetização.
    """
    channel = session.get(Channel, job.channel_id)
    if not channel:
        return None

    # 1. Monetização Dupla: Tenta buscar ClickBank e Amazon simultaneamente
    def test_link_safe(url: str) -> bool:
        if not url: return False
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
            # Algumas páginas de vendas bloqueiam bots, mas o ClickBank Redirect (hop) deve ser 200/302
            r = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
            
            # Se a URL final for uma página de erro do ClickBank, o link é inválido
            error_patterns = [
                "error-page", "invalid-hoplink", "clickbank.net/error", 
                "upsell-error", "not-found", "404", "suspended", "inactive",
                "vendoraccntstate", "errcode"
            ]
            final_url = r.url.lower()
            if any(p in final_url for p in error_patterns):
                return False
                
            return r.status_code == 200 or r.status_code == 302
        except Exception:
            return True # Fallback otimista se houver timeout (pode ser o firewall do vendor)

    final_links = []
    
    # Busca ClickBank (Prioridade US)
    if channel.language == "en-US":
        try:
            from agents.monetization.clickbank_scout import ClickBankScout
            scout_cb = ClickBankScout(session)
            cb_prod = scout_cb.find_and_cache_product(job.topic, channel.niche, channel.language, channel.tenant_id)
            if cb_prod:
                cb_url = inject_tracking_link(cb_prod, job.id, channel.id)
                if test_link_safe(cb_url):
                    final_links.append(f"🔥 Recommended Tool/Software: {cb_url}")
        except Exception as e:
            print(f"⚠️ Erro no ClickBank Scout: {e}")

    # Busca Digistore24 (Prioridade Europa/International)
    if channel.language in ["fr-FR", "es-ES", "de-DE", "en-GB"]:
        try:
            from agents.monetization.digistore_scout import DigistoreScout
            scout_dg = DigistoreScout(session)
            dg_prod = scout_dg.find_and_cache_product(job.topic, channel.niche, channel.language, channel.tenant_id)
            if dg_prod:
                dg_url = inject_tracking_link(dg_prod, job.id, channel.id)
                if test_link_safe(dg_url):
                    final_links.append(f"💎 Premium Solution: {dg_url}")
        except Exception as e:
            print(f"⚠️ Erro no Digistore Scout: {e}")

    # Busca Amazon
    try:
        from agents.monetization.amazon_scout import AmazonScout
        scout_amz = AmazonScout(session)
        amz_prod = scout_amz.find_and_cache_product(job.topic, channel.niche, channel.language, channel.tenant_id)
        if amz_prod:
            amz_url = inject_tracking_link(amz_prod, job.id, channel.id)
            if test_link_safe(amz_url):
                final_links.append(f"📚 Best Book/Hardware: {amz_url}")
            else:
                print(f"⚠️ Link Amazon inválido/404 descartado: {amz_url}")
    except Exception as e:
        print(f"⚠️ Erro no Amazon Scout: {e}")

    # 1.5 FALLBACKS DE SEGURANÇA (Links fornecidos pelo Usuário para garantir monetização)
    if not final_links:
        print(f"🔄 [MONETIZAÇÃO] Nenhum link dinâmico encontrado para {channel.language}. Usando Fallback Estruturado...")
        hardcoded_fallbacks = {
            "en-US": [
                "🔥 Recommended Tool: https://f10b5twm8kg1tb30zalgzico7h.hop.clickbank.net/?&traffic_source=youtube&campaign=canalDarkIABusiness",
                "🚀 Scaling Solution: https://136a6tqf-kq9ne09oj8grjqc5x.hop.clickbank.net/?&traffic_source=youtube&campaign=canalDarkIABusiness",
                "📚 Best AI Book: https://www.amazon.com/dp/1492041130/?tag=canaldarkdjm-20"
            ],
            "es-ES": [
                "🔥 Herramienta IA Recomendada: https://adaptichat.com/#aff=AIBusinessDK",
                "💎 Solución Premium: https://www.digistore24.com/redir/554599/AIBusinessDK/",
                "📚 Libro Recomendado: https://www.amazon.es/dp/B0FF27LR6L?tag=canaldarkdjm-20"
            ],
            "fr-FR": [
                "📚 Meilleur Livre IA: https://www.amazon.fr/dp/B0FY7KYPGH?tag=canaldarkdjm-20",
                "💎 Solution Premium: https://www.digistore24.com/redir/299134/AIBusinessDK"
            ],
            "de-DE": [
                "🔥 KI-System Empfehlung: https://aifluencersystem.de/start#aff=AIBusinessDK",
                "📚 Bestes KI-Buch: https://www.amazon.de/dp/336710289X?tag=canaldarkdjm-20"
            ]
        }
        
        fallback_list = hardcoded_fallbacks.get(channel.language, [])
        if fallback_list:
            final_links.extend(fallback_list)
            print(f"✅ [FALLBACK] {len(fallback_list)} links injetados para {channel.language}")

    affiliate_url = "\n".join(final_links)
    job.injected_affiliate_url = affiliate_url
    if final_links:
        print(f"💰 Monetization Injected:\n{affiliate_url}")

    # 2. IA: Busca contexto de vídeos virais anteriores (Loop de Aprendizado)
    optimization_notes = "Standard Viral Patterns: High pacing, aggressive hook, pattern interrupt."
    import os
    elite_examples_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "channel_setup", "assets", "elite_script_examples.md")
    if os.path.exists(elite_examples_path):
        with open(elite_examples_path, "r", encoding="utf-8") as f:
            optimization_notes += "\n\n" + f.read()
    
    import json
    
    # Prevenção de alucinação de links
    link_instruction = ""
    if not affiliate_url:
        link_instruction = "IMPORTANT: No affiliate product available. DO NOT mention any link."
    else:
        link_instruction = f"The affiliate link to promote is: {affiliate_url}. Mention it naturally."

    # ───────────────────────────────────────────────────────
    # 2. BUILD PROMPT via Retention Engine
    # ───────────────────────────────────────────────────────
    current_date = datetime.now(timezone.utc).strftime("%B %Y")
    is_short = job.video_type == "SHORT"
    num_scenes = 5 if is_short else 22

    system_prompt, prompt = build_retention_prompt(
        topic=job.topic,
        target_language=job.target_language or channel.language or "en-US",
        is_short=is_short,
        affiliate_url=affiliate_url or "",
        optimization_notes=optimization_notes,
        current_date=current_date,
        num_scenes=num_scenes,
    )

    prompt += (
        f"\n\nCONTEXTO TEMPORAL: Hoje é {current_date}. "
        f"PROIBIDO citar 2024 ou 2025 como 'o futuro'. O ano atual é 2026."
    )
    
    # ───────────────────────────────────────────────────────
    # 3. LLM GENERATION LOOP (with density + validator)
    # ───────────────────────────────────────────────────────
    script_data = {}
    for attempt in range(4):
        raw_response = get_completion(
            prompt=prompt,
            system_instruction=system_prompt,
            json_mode=True,
            max_tokens=6000
        )
        
        print(f"DEBUG (Attempt {attempt+1}): Resposta Raw da IA (primeiros 200 chars): {raw_response[:200]}...")
        
        clean_json = orchestrator._clean_json(raw_response)
        try:
            script_data = json.loads(clean_json)
            # Normalização preliminar das cenas para contar palavras
            scenes = script_data.get("scenes", script_data.get("Cenas", []))
            if not isinstance(scenes, list): scenes = []
            
            total_text = ""
            for s in scenes:
                if isinstance(s, dict):
                    total_text += " " + str(s.get("narration") or s.get("text") or s.get("texto") or s.get("content") or "")
            
            word_count = len(total_text.split())

            # ── Validator: remove duplicates BEFORE density check ──
            validation_result = None
            if not is_short:
                script_data, validation_result = validate_and_fix_script(script_data, is_short=False)
                # Re-count after deduplication
                scenes_clean = script_data.get("scenes", [])
                total_text = " ".join(
                    str(s.get("narration") or s.get("text") or s.get("content") or "")
                    for s in scenes_clean if isinstance(s, dict)
                )
                word_count = len(total_text.split())

            # Density gate
            if not is_short:
                if word_count >= 1200:
                    print(f"✅ Script validado: {word_count} palavras | Duplicatas: {len(validation_result.scenes_removed) if validation_result else 0} removidas")
                    if validation_result and not validation_result.strong_hook:
                        print("⚠️  HOOK FRACO detectado pelo Validator. Considere regenerar.")
                    break
                else:
                    print(f"⚠️ Script muito curto ({word_count} palavras). Solicitando expansão (Tentativa {attempt+1}/4)...")
                    prompt += (
                        f"\n\nCRITICAL: Only {word_count} words generated. "
                        f"Each scene MUST have 60-75 words of narration. "
                        f"Target: {num_scenes} scenes × 65 words = ~{num_scenes*65} words total."
                    )
            else:
                break # Shorts não precisam de validação de densidade longa
        except Exception as e:
            print(f"⚠️ Erro ao parsear JSON na tentativa {attempt+1}: {e}")
            if attempt == 3: raise e
    
    try:
        # Normalização de Chaves (Anti-Alucinação)
        normalized = {}
        for k, v in script_data.items():
            k_low = k.lower()
            if any(x in k_low for x in ["scene", "cena", "step"]): normalized["scenes"] = v
            elif any(x in k_low for x in ["title", "titulo"]): normalized["title"] = v
            elif "desc" in k_low: normalized["description"] = v
            elif "tag" in k_low: normalized["tags"] = v
            else: normalized[k] = v
        
        # Saneamento profundo das cenas para garantir 'narration' em todas
        final_scenes = normalized.get("scenes", [])
        if isinstance(final_scenes, list):
            for s in final_scenes:
                if isinstance(s, dict):
                    # Tenta encontrar a narração em diversas chaves comuns
                    narr = s.get("narration") or s.get("text") or s.get("texto") or s.get("content") or ""
                    s["narration"] = narr
        
        # Se as cenas vieram vazias mas temos narração em algum lugar, cria fallback
        if not final_scenes or not isinstance(final_scenes, list):
            print("⚠️ JSON sem cenas detectado. Criando cenas de fallback longas...")
            narration = normalized.get("narration") or normalized.get("script") or normalized.get("script_full") or f"This is an deep dive into {job.topic}. We are exploring everything you need to know about the secrets behind this theme. Stay tuned for the final revelation."
            if isinstance(narration, list): narration = " ".join(narration)
            
            # Divide o texto em pelo menos 3 cenas para garantir duração
            final_scenes = [
                {"timestamp": "0:00-0:10", "narration": str(narration), "b_roll_visual": job.topic},
                {"timestamp": "0:10-0:20", "narration": "Continuing our exploration into these unknown facts...", "b_roll_visual": "cinematic tech"},
                {"timestamp": "0:20-0:40", "narration": "Finally, we understand that the truth is much deeper than it seems.", "b_roll_visual": "epic landscape"}
            ]
        # 3. Salva os dados estruturados no Job (Incluindo Estratégia Elite)
        job.topic = normalized.get("title", job.topic)
        job.script_data = sanitize_script_glossary({
            "title": job.topic,
            "marketing_strategy": normalized.get("marketing_strategy", {}),
            "description": normalized.get("description", ""),
            "tags": normalized.get("tags", []),
            "script_full": "\n".join([str(s.get("narration", "")) for s in final_scenes]),
            "scenes": final_scenes,
            "music_mood": normalized.get("music_mood", "suspense"),
            "language": job.target_language or channel.language or "en-US",
            "cta_link": affiliate_url,
            "quiz": normalized.get("quiz", None),
            "generated_at": str(datetime.now(timezone.utc))
        })
    except Exception as e:
        print(f"⚠️ Erro ao parsear JSON do script: {e}")
        import re
        
        # Recuperação de Emergência (Caso a IA quebre a pontuação do JSON)
        # Extrai os textos diretamente das aspas escapando caracteres
        narrations = re.findall(r'"narration"\s*:\s*"((?:[^"\\]|\\.)*)"', clean_json)
        visuals = re.findall(r'"b_roll_visual"\s*:\s*"((?:[^"\\]|\\.)*)"', clean_json)
        
        # Tenta recuperar descrição e tags via regex também
        # Busca a descrição mesmo que esteja truncada (sem fechar aspas)
        desc_match = re.search(r'"description"\s*:\s*"(.*?)(?:"\s*[,}]|(?:"\s*,\s*"tags")|$)', clean_json, re.DOTALL)
        recovered_desc = desc_match.group(1).replace('\\"', '"').replace('\\n', '\n') if desc_match else "No description recovered."
        
        # Se a descrição ainda parece muito curta e temos o script_full, usa o início dele como fallback
        if len(recovered_desc) < 50 and "narration" in clean_json:
            recovered_desc = f"Descubra tudo sobre {job.topic}. Neste vídeo, mergulhamos fundo nos detalhes, estratégias e segredos que você precisa saber agora!"
        
        tags_match = re.findall(r'"tags"\s*:\s*\[(.*?)\]', clean_json, re.DOTALL)
        recovered_tags = []
        if tags_match:
            recovered_tags = [t.strip().strip('"') for t in tags_match[0].split(",")]

        if narrations and len(narrations) > 5:
            print(f"🛠️ [AUTO-HEAL] Recuperando {len(narrations)} cenas via Regex Profundo!")
            recovered_scenes = []
            for i, n in enumerate(narrations):
                # Desescapar possíveis barras invertidas do Regex
                clean_narration = n.replace('\\"', '"').replace('\\n', ' ')
                v = visuals[i].replace('\\"', '"') if i < len(visuals) else "cinematic and dramatic dark channel visual"
                recovered_scenes.append({"timestamp": f"{i*10}-{i*10+10}s", "narration": clean_narration, "b_roll_visual": v})
            
            job.script_data = sanitize_script_glossary({
                "title": job.topic,
                "description": recovered_desc,
                "tags": recovered_tags,
                "script_full": "\n".join([s["narration"] for s in recovered_scenes]),
                "scenes": recovered_scenes,
                "cta_link": affiliate_url,
                "quiz": None,
                "generated_at": str(datetime.now(timezone.utc))
            })
        else:
            # Fallback para evitar aborto do Worker se falhar absurdamente
            job.script_data = sanitize_script_glossary({
                "title": job.topic,
                "description": f"Masterclass sobre {job.topic}. Estratégias avançadas e insights exclusivos para você dominar este nicho.",
                "tags": [job.topic.split()[-1], "AI", "Automation", "Success"],
                "script_full": f"Welcome back. Today we are diving into {job.topic}. This is a complete breakdown of everything you need to succeed.",
                "scenes": [
                    {"timestamp": "0:00-0:10", "narration": f"This is everything you need to know about {job.topic}.", "b_roll_visual": "cinematic professional background"}
                ],
                "cta_link": affiliate_url,
                "generated_at": str(datetime.now(timezone.utc))
            })
    
    # 4. Se o tenant tem auto_approve, já manda pro Worker
    tenant = session.get(Tenant, channel.tenant_id)
    if tenant and tenant.auto_approve_content:
        job.status = "APPROVED"
    else:
        job.status = "DRAFTED"
    
    session.add(job)
    session.commit()
    return job
