from typing import Dict, Any
from uuid import UUID
from sqlmodel import Session
from cloud_hub.models import VideoJob, Channel, Tenant
from cloud_hub.core.monetization import get_best_match_product, inject_tracking_link
from shared.llm_client import get_completion
from shared.ai_orchestrator import orchestrator
from datetime import datetime, timezone

SYSTEM_PROMPT = """
You are a Senior Viral Video Director and Retention Engineering Architect (LEVEL 100/100).
Your goal is to transform "AI Slop" into Premium, Cinematic, High-Authority Business Documentaries.

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

You are writing for a top-tier faceless AI business channel. 
The goal is HIGH-CTR, HIGH-RETENTION, and ALGORITHMIC OBSESSION.

## 🎯 VIDEO OBJECTIVES
- **HOOK (0-5s):** Start with a shock in {target_language}.
- **RETENTION:** Create curiosity loops. Every scene must lead to the next "payoff".
- **STORYTELLING:** Follow a structure of: 1. Curiosity -> 2. Tension -> 3. Escalation -> 4. Reveal -> 5. Next Loop.
- **VISUALS:** b_roll_visual must ALWAYS be in ENGLISH for search compatibility.

## 🚀 OUTPUT REQUIREMENTS:
1. **ALL TEXT FIELDS (narration, description, title, marketing_strategy, quiz, thumb_text_vars) MUST BE WRITTEN IN {target_language}.**
2. **ALL B_ROLL_VISUAL FIELDS MUST BE WRITTEN IN ENGLISH.**
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
            r = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
            
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
    # TODO: No futuro, buscar aqui do banco os top 20% que performaram bem
    
    import json
    
    # Prevenção de alucinação de links
    link_instruction = ""
    if not affiliate_url:
        link_instruction = "IMPORTANT: No affiliate product available. DO NOT mention any link."
    else:
        link_instruction = f"The affiliate link to promote is: {affiliate_url}. Mention it naturally."

    # Instruções de duração baseadas no tipo de vídeo
    is_short = job.video_type == "SHORT"
    if is_short:
        dur_instr = "Criar um roteiro para um SHORT VIRAL de EXATAMENTE 59 SEGUNDOS. LIMITE ABSOLUTO: 80 a 100 palavras NO TOTAL (script completo). MANDATÓRIO: Exatamente 5 a 6 cenas com 1 frase curta cada. PROIBIDO ultrapassar 100 palavras."
        narr_instr = "ULTRA CURTA: exatamente 1 frase de alto impacto por cena (máximo 20 palavras por cena). Sem texto longo."

    else:
        dur_instr = "Criar um vídeo denso e altamente técnico focado em bater entre 12 e 15 minutos de narração (Obrigatório: mínimo absoluto de 2200 palavras totais). MANDATÓRIO: O vídeo DEVE ter exatamente 35 cenas detalhadas. Insira obrigatoriamente um 'Mini-Tutorial' ou 'Estudo de Caso Prático' na metade do roteiro."
        narr_instr = "um TEXTO LONGO, EXTREMAMENTE DENSO e DESCRITIVO contendo rigorosamente ENTRE 100 e 120 PALAVRAS por cena. PROIBIDO escrever frases curtas ou tópicos. Cada cena deve ser um parágrafo robusto."

    # Prepara o prompt com as notas de otimização e idioma
    # INJETAMOS A DATA ATUAL PARA EVITAR ROTEIROS DE "2025"
    current_date = datetime.now(timezone.utc).strftime("%B %Y")
    
    prompt = MASTER_SCRIPT_PROMPT.format(
        topic=job.topic, 
        affiliate_url=affiliate_url or "NONE",
        optimization_notes=optimization_notes,
        target_language=job.target_language or channel.language or "en-US",
        duration_instructions=dur_instr,
        narration_instructions=narr_instr
    ) + f"\n\nCONTEXTO TEMPORAL: Hoje é {current_date}. Foque em tendências e dados dos últimos 30 dias. PROIBIDO citar 2024 ou 2025 como 'o futuro' ou 'este ano'. O ano atual é 2026." + f"\n\n{link_instruction}"
    
    # Loop de Verificação de Densidade (Garante 12+ minutos)
    script_data = {}
    for attempt in range(4):
        raw_response = get_completion(
            prompt=prompt,
            system_instruction=SYSTEM_PROMPT,
            json_mode=True
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
            
            # Validação de contagem de palavras para vídeos longos
            if not is_short:
                if word_count >= 1900:
                    print(f"✅ Script validado com densidade suficiente: {word_count} palavras.")
                    break
                else:
                    print(f"⚠️ Script muito curto ({word_count} palavras). Solicitando expansão massiva (Tentativa {attempt+1}/4)...")
                    prompt += f"\n\nCRITICAL: The previous response was only {word_count} words. YOU MUST WRITE LONGER PARAGRAPHS. EACH SCENE NARRATION MUST BE A FULL DENSE BLOCK OF 120-150 WORDS. TARGET 2500 WORDS TOTAL. Do not repeat content, expand on the technical details and case studies."
            else:
                break # Shorts não precisam de validação de densidade longa
        except Exception as e:
            print(f"⚠️ Erro ao parsear JSON na tentativa {attempt+1}: {e}")
            if attempt == 1: raise e
    
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
        job.script_data = {
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
        }
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
            
            job.script_data = {
                "title": job.topic,
                "description": recovered_desc,
                "tags": recovered_tags,
                "script_full": "\n".join([s["narration"] for s in recovered_scenes]),
                "scenes": recovered_scenes,
                "cta_link": affiliate_url,
                "quiz": None,
                "generated_at": str(datetime.now(timezone.utc))
            }
        else:
            # Fallback para evitar aborto do Worker se falhar absurdamente
            job.script_data = {
                "title": job.topic,
                "description": f"Masterclass sobre {job.topic}. Estratégias avançadas e insights exclusivos para você dominar este nicho.",
                "tags": [job.topic.split()[-1], "AI", "Automation", "Success"],
                "script_full": f"Welcome back. Today we are diving into {job.topic}. This is a complete breakdown of everything you need to succeed.",
                "scenes": [
                    {"timestamp": "0:00-0:10", "narration": f"This is everything you need to know about {job.topic}.", "b_roll_visual": "cinematic professional background"}
                ],
                "cta_link": affiliate_url,
                "generated_at": str(datetime.now(timezone.utc))
            }
    
    # 4. Se o tenant tem auto_approve, já manda pro Worker
    tenant = session.get(Tenant, channel.tenant_id)
    if tenant and tenant.auto_approve_content:
        job.status = "APPROVED"
    else:
        job.status = "DRAFTED"
    
    session.add(job)
    session.commit()
    return job
