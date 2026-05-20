import os
import time
import requests
from typing import List, Dict, Any, Optional
from openai import OpenAI
from shared.logger import get_logger
from dotenv import load_dotenv

load_dotenv()
logger = get_logger(__name__)

class LLMResponse:
    def __init__(self, text: str, provider: str, model: str, tokens: int = 0):
        self.text = text
        self.provider = provider
        self.model = model
        self.tokens = tokens
        self.timestamp = time.time()

    def __repr__(self):
        return f"<LLMResponse from {self.provider} ({self.model})>"

class AIOrchestrator:
    """
    Orquestrador de IA de Missão Crítica para Dark Channel Factory.
    Gerencia contingência entre Gemini, Groq, OpenRouter e Mistral.
    """

    def __init__(self):
        self.providers_status = {
            "gemini": {"active": True, "last_failure": 0},
            "groq": {"active": True, "last_failure": 0},
            "openai": {"active": True, "last_failure": 0},
            "openrouter": {"active": True, "last_failure": 0},
            "deepseek": {"active": True, "last_failure": 0},
            "mistral": {"active": True, "last_failure": 0},
        }
        self.circuit_break_time = 300  # 5 minutos de cooldown após erro grave

        # Carrega modelos das ENVs e limpa espaços/quebras de linha do Windows
        self.primary_model = os.getenv("LLM_PRIMARY_MODEL", "gemini-3.5-flash").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
        self.openai_model = os.getenv("LLM_FINAL_FALLBACK_MODEL", "gpt-4o-mini").strip()
        self.or_model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-haiku").strip()

        # Inicialização dos Clientes
        self._init_clients()

    def _init_clients(self):
        # Gemini (REST)
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key:
            self.gemini_key = self.gemini_key.strip()
        
        # OpenAI
        self.openai_key = os.getenv("OPENAI_API_KEY")
        if self.openai_key:
            self.openai_key = self.openai_key.strip()
        self.openai_client = OpenAI(api_key=self.openai_key, max_retries=0) if self.openai_key else None
        
        # Groq
        self.groq_key = os.getenv("GROQ_API_KEY")
        if self.groq_key:
            self.groq_key = self.groq_key.strip()
        self.groq_client = OpenAI(api_key=self.groq_key, base_url="https://api.groq.com/openai/v1", max_retries=0) if self.groq_key else None
        
        # OpenRouter
        self.or_key = os.getenv("OPENROUTER_API_KEY")
        if self.or_key:
            self.or_key = self.or_key.strip()
        self.or_client = OpenAI(
            api_key=self.or_key, 
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
            default_headers={"HTTP-Referer": "https://dark-channel-factory.local", "X-Title": "Dark Channel Factory"}
        ) if self.or_key else None

        # DeepSeek Nativo
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if self.deepseek_key:
            self.deepseek_key = self.deepseek_key.strip()
        self.deepseek_client = OpenAI(api_key=self.deepseek_key, base_url="https://api.deepseek.com/v1", max_retries=0) if self.deepseek_key else None

        # Mistral
        self.mistral_key = os.getenv("MISTRAL_API_KEY")
        if self.mistral_key:
            self.mistral_key = self.mistral_key.strip()
        self.mistral_client = OpenAI(api_key=self.mistral_key, base_url="https://api.mistral.ai/v1", max_retries=0) if self.mistral_key else None

    def _is_provider_available(self, provider_id: str) -> bool:
        status = self.providers_status.get(provider_id)
        if not status["active"]:
            cooldown = status.get("cooldown", self.circuit_break_time)
            if time.time() - status["last_failure"] > cooldown:
                logger.info(f"🔄 Circuit Breaker: Reativando {provider_id} após cooldown...")
                status["active"] = True
                return True
            return False
        return True

    def _mark_failure(self, provider_id: str, reason: str, is_quota: bool = False, is_rate_limit: bool = False):
        """Desativa provedor. Quota/cred errors ficam 4h offline, rate-limits 1 min, outros 5 min."""
        if is_quota:
            cooldown = 14400 # 4h
        elif is_rate_limit:
            cooldown = 60    # 1 min
        else:
            cooldown = 300   # 5 min

        logger.warning(f"🚨 Falha [{provider_id}]: {reason[:80]}. Cooldown={cooldown}s.")
        self.providers_status[provider_id]["active"] = False
        self.providers_status[provider_id]["last_failure"] = time.time()
        self.providers_status[provider_id]["cooldown"] = cooldown

    def _clean_json(self, text: str) -> str:
        """Extrai o conteúdo JSON de uma string poluída e tenta consertar erros comuns de LLMs."""
        text = text.strip()
        
        # Remove blocos de código markdown se existirem
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        # Procura o início do JSON
        start_dict = text.find('{')
        start_list = text.find('[')
        
        if start_dict == -1 and start_list == -1:
            return text
            
        start = min(start_dict, start_list) if (start_dict != -1 and start_list != -1) else (start_dict if start_dict != -1 else start_list)
        
        # Procura o fim correspondente
        end_dict = text.rfind('}')
        end_list = text.rfind(']')
        end = max(end_dict, end_list)
        
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end+1]
            
            # Reparo Rápido: Substitui quebras de linha dentro de strings (erro comum)
            # candidate = candidate.replace('\n', ' ') # Cuidado: pode quebrar se for necessário
            
            import json
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError as e:
                # Tenta consertar vírgulas faltantes em listas de objetos
                if "Expecting ',' delimiter" in str(e):
                    # Tenta um reparo agressivo: insere vírgula entre } e {
                    import re
                    candidate = re.sub(r'}\s*{', '}, {', candidate)
                    try:
                        json.loads(candidate)
                        return candidate
                    except: pass
                
                # Recuperação de Truncamento
                fixed_text = text[start:]
                brace_count = fixed_text.count('{') - fixed_text.count('}')
                bracket_count = fixed_text.count('[') - fixed_text.count(']')
                if bracket_count > 0: fixed_text += ']' * bracket_count
                if brace_count > 0: fixed_text += '}' * brace_count
                
                try:
                    json.loads(fixed_text)
                    return fixed_text
                except:
                    return text[start:end+1]
        
        return text

    def chat_completion(self, system_prompt: str, user_prompt: str, json_mode: bool = False, max_tokens: int = 8192) -> LLMResponse:
        """
        Executa a chamada LLM com fallback automático seguindo a Tier List e Cache de Custo-Zero.
        """
        import hashlib
        import json
        
        # Otimização de Custos: Cache Agressivo
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets_cache", "llm_cache")
        os.makedirs(cache_dir, exist_ok=True)
        prompt_hash = hashlib.md5((system_prompt + user_prompt).encode()).hexdigest()
        cache_file = os.path.join(cache_dir, f"{prompt_hash}.json")
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                logger.info("⚡ [CACHE SEMÂNTICO] Retornando roteiro do cache (Custo Zero)!")
                return LLMResponse(cached_data["text"], "Cache", "semantic-cache")
            except: pass

        def save_to_cache(text: str):
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump({"text": text}, f, ensure_ascii=False)
            except: pass

        # TIER 1: Gemini 3.5 Flash (Primário) → Gemini 2.5 Flash (Fallback)
        GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
        if self._is_provider_available("gemini") and self.gemini_key:
            try:
                resp = self._call_gemini(self.primary_model, system_prompt, user_prompt, json_mode, max_tokens)
                if json_mode: resp.text = self._clean_json(resp.text)
                save_to_cache(resp.text)
                return resp
            except Exception as e:
                err_str = str(e).lower()
                # Se o modelo primário retornou 404, tenta um modelo diferente como fallback
                if ("404" in err_str or "not found" in err_str) and self.primary_model != GEMINI_FALLBACK_MODEL:
                    logger.warning(f"⚠️ Gemini model '{self.primary_model}' returned 404. Trying fallback '{GEMINI_FALLBACK_MODEL}'...")
                    try:
                        resp = self._call_gemini(GEMINI_FALLBACK_MODEL, system_prompt, user_prompt, json_mode, max_tokens)
                        if json_mode: resp.text = self._clean_json(resp.text)
                        save_to_cache(resp.text)
                        return resp
                    except Exception as e2:
                        err_str = str(e2).lower()
                        e = e2  # Usa o erro do fallback para o log
                
                is_rate_limit = "429" in err_str or "rate limit" in err_str or "quota exceeded" in err_str
                is_quota = "insufficient_quota" in err_str or "402" in err_str
                self._mark_failure("gemini", str(e), is_quota=is_quota, is_rate_limit=is_rate_limit)
                if is_rate_limit: time.sleep(1)

        # TIER 2: Groq (Llama 3 70B - Excelente cota gratuita e ultra-rápido)
        # NOTA: Groq tem limite de 6000 tokens de output por request para llama-3.3-70b
        groq_max_tokens = min(max_tokens, 6000)
        if self._is_provider_available("groq") and self.groq_client:
            try:
                resp = self._call_openai_compatible(
                    self.groq_client, self.groq_model, "Groq", system_prompt, user_prompt, json_mode, groq_max_tokens
                )
                if json_mode: resp.text = self._clean_json(resp.text)
                save_to_cache(resp.text)
                return resp
            except Exception as e:
                err_str = str(e).lower()
                # 413 = payload muito grande → tenta com modelo mais leve
                if "413" in err_str or "too large" in err_str or "payload" in err_str:
                    logger.warning("⚠️ Groq 413: Payload muito grande. Tentando com llama-3.1-8b-instant...")
                    try:
                        resp = self._call_openai_compatible(
                            self.groq_client, "llama-3.1-8b-instant", "Groq-8B", system_prompt, user_prompt, json_mode, 4000
                        )
                        if json_mode: resp.text = self._clean_json(resp.text)
                        save_to_cache(resp.text)
                        return resp
                    except Exception as e2:
                        err_str = str(e2).lower()
                        e = e2
                is_rate_limit = "429" in err_str or "rate_limit" in err_str
                is_quota = "insufficient_quota" in err_str or "402" in err_str or "credits" in err_str
                self._mark_failure("groq", str(e), is_quota=is_quota, is_rate_limit=is_rate_limit)
                if is_rate_limit: time.sleep(1) # Pequeno fôlego antes do próximo provider

        # TIER 3: OpenAI (GPT-4o mini)
        if self._is_provider_available("openai") and self.openai_client:
            try:
                resp = self._call_openai_compatible(
                    self.openai_client, self.openai_model, "OpenAI", system_prompt, user_prompt, json_mode, max_tokens
                )
                if json_mode: resp.text = self._clean_json(resp.text)
                save_to_cache(resp.text)
                return resp
            except Exception as e:
                err_str = str(e).lower()
                is_rate_limit = "429" in err_str or "rate_limit" in err_str
                is_quota = "insufficient_quota" in err_str or "402" in err_str
                self._mark_failure("openai", str(e), is_quota=is_quota, is_rate_limit=is_rate_limit)
                if is_rate_limit: time.sleep(1)

        # TIER 3: OpenRouter
        if self._is_provider_available("openrouter") and self.or_client:
            try:
                resp = self._call_openai_compatible(
                    self.or_client, self.or_model, "OpenRouter", system_prompt, user_prompt, json_mode, max_tokens
                )
                if json_mode: resp.text = self._clean_json(resp.text)
                save_to_cache(resp.text)
                return resp
            except Exception as e:
                is_quota = "402" in str(e) or "429" in str(e) or "credits" in str(e).lower()
                self._mark_failure("openrouter", str(e), is_quota=is_quota)

        # TIER 4: DeepSeek Nativo
        if self._is_provider_available("deepseek") and self.deepseek_client:
            try:
                resp = self._call_openai_compatible(
                    self.deepseek_client, "deepseek-chat", "DeepSeek", system_prompt, user_prompt, json_mode, max_tokens
                )
                if json_mode: resp.text = self._clean_json(resp.text)
                save_to_cache(resp.text)
                return resp
            except Exception as e:
                is_quota = "402" in str(e) or "Insufficient Balance" in str(e)
                self._mark_failure("deepseek", str(e), is_quota=is_quota)

        # TIER 5: Mistral (Final Fallback)
        if self._is_provider_available("mistral") and self.mistral_client:
            try:
                resp = self._call_openai_compatible(
                    self.mistral_client, "mistral-small-latest", "MistralAI", system_prompt, user_prompt, json_mode, max_tokens
                )
                if json_mode: resp.text = self._clean_json(resp.text)
                return resp
            except Exception as e:
                is_quota = "429" in str(e) or "402" in str(e)
                self._mark_failure("mistral", str(e), is_quota=is_quota)

        raise RuntimeError("CRITICAL ERROR: Todos os provedores de LLM falharam ou estão offline.")

    def _call_gemini(self, model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> LLMResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_key}"
        
        full_prompt = f"SYSTEM: {system}\n\nUSER: {user}"
        if json_mode:
            full_prompt += "\n\nIMPORTANT: Return ONLY valid JSON."

        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens}
        }
        
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 429:
            raise Exception("Quota Exceeded")
        if response.status_code != 200:
            raise Exception(f"API Error {response.status_code}")

        data = response.json()
        text = data['candidates'][0]['content']['parts'][0]['text']
        tokens = data.get('usageMetadata', {}).get('totalTokenCount', 0)
        
        logger.info(f"✅ Gemini Success: {model}")
        return LLMResponse(text, "Gemini", model, tokens)

    def _call_openai_compatible(self, client: OpenAI, model: str, provider: str, system: str, user: str, json_mode: bool, max_tokens: int) -> LLMResponse:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "timeout": 90,  # 90s timeout to prevent hung requests
        }
        if json_mode:
            kwargs["messages"][0]["content"] += "\nReturn ONLY JSON."
            # Enable JSON mode for providers that support it reliably
            if provider.lower() in ["groq", "openai", "mistralai"]:
                kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0
        
        logger.info(f"✅ {provider} Success: {model}")
        return LLMResponse(text, provider, model, tokens)

# Singleton para uso no FastAPI
orchestrator = AIOrchestrator()
