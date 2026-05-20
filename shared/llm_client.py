"""
LLM Client — Dark Channel Factory
Gerencia cascata: Gemini 2.0+ → DeepSeek → OpenAI
"""
from __future__ import annotations

import os
import time
from typing import Any

from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────
GEMINI_PRIMARY = os.getenv("LLM_PRIMARY_MODEL", "gemini-3.5-flash").strip()
GEMINI_FALLBACK = os.getenv("LLM_FALLBACK_MODEL", "gemini-2.5-flash").strip()
OPENAI_FALLBACK = os.getenv("LLM_FINAL_FALLBACK", "gpt-4o-mini").strip()
MOCK_LLM = os.getenv("MOCK_LLM", "false").lower() == "true"


class LLMResponse(object):
    """Wrapper unificado para respostas de LLM."""

    def __init__(self, text: str, model_used: str, tokens_used: int = 0):
        self.text = text
        self.model_used = model_used
        self.tokens_used = tokens_used

    def __repr__(self) -> str:
        return f"LLMResponse(model={self.model_used}, chars={len(self.text)})"


class LLMClient:
    """
    Cliente LLM com cascata automática de modelos:
    1. Google Gemini 2.5 Flash (primário, mais barato)
    2. Google Gemini 1.5 Flash (fallback Gemini)
    3. OpenAI GPT-4o-mini (fallback final)
    """

    def __init__(self) -> None:
        self._gemini_client = None
        self._openai_client = None
        self._deepseek_client = None
        self._init_clients()

    def _init_clients(self) -> None:
        """Inicializa clientes disponíveis silenciosamente."""
        # Gemini (Usando REST API direta para evitar SDK deprecated)
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_key and gemini_key != "your_gemini_api_key_here":
            self._gemini_api_key = gemini_key
            self._gemini_client = True # Flag para indicar que está disponível
            logger.debug(f"Gemini REST client initialized (primary: {GEMINI_PRIMARY})")
        else:
            logger.warning("GEMINI_API_KEY não configurada — Gemini indisponível")

        # DeepSeek (OpenAI-compatible)
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key and deepseek_key != "your_deepseek_key_here":
            try:
                from openai import OpenAI
                self._deepseek_client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
                logger.debug("DeepSeek client initialized (high-priority fallback)")
            except ImportError:
                logger.warning("openai not installed — DeepSeek unavailable")

        # OpenAI (DALL-E 3 & Fallback)
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        if openai_key and openai_key != "your_openai_api_key_here":
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=openai_key)
                logger.debug("OpenAI client initialized (DALL-E 3 + Fallback)")
            except ImportError:
                logger.warning("openai not installed — OpenAI unavailable")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(Exception),
    )
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """
        Chama o LLM com cascata automática.
        Se MOCK_LLM=true, retorna resposta mock para testes.
        """
        if MOCK_LLM:
            return self._mock_response(user_prompt)

        # Tentativa 1: Gemini Primary
        if self._gemini_client:
            try:
                return self._call_gemini(
                    GEMINI_PRIMARY, system_prompt, user_prompt, temperature, max_tokens, json_mode
                )
            except Exception as e:
                logger.warning(f"Gemini primary ({GEMINI_PRIMARY}) failed: {e}")

        # Tentativa 2: DeepSeek (Custo-benefício imbatível)
        if self._deepseek_client:
            try:
                # Usa deepseek-chat por padrão
                model = "deepseek-chat"
                return self._call_openai(
                    model, system_prompt, user_prompt, temperature, max_tokens, json_mode, client=self._deepseek_client
                )
            except Exception as e:
                logger.warning(f"DeepSeek failed: {e}")

        # Tentativa 3: Gemini Fallback
        if self._gemini_client:
            try:
                return self._call_gemini(
                    GEMINI_FALLBACK, system_prompt, user_prompt, temperature, max_tokens, json_mode
                )
            except Exception as e:
                logger.warning(f"Gemini fallback ({GEMINI_FALLBACK}) failed: {e}")

        # Tentativa 4: OpenAI (Final fallback)
        if self._openai_client:
            try:
                return self._call_openai(
                    OPENAI_FALLBACK, system_prompt, user_prompt, temperature, max_tokens, json_mode
                )
            except Exception as e:
                logger.error(f"OpenAI fallback also failed: {e}")
                raise RuntimeError("All LLM providers failed") from e

        raise RuntimeError(
            "No LLM clients available. Check GEMINI_API_KEY, DEEPSEEK_API_KEY or OPENAI_API_KEY in .env"
        )

    def _call_gemini(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        import requests
        
        # Usando v1beta para garantir compatibilidade com os modelos 2.0 e 2.5
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self._gemini_api_key}"
        
        # Formato Universal (Merged Prompt) para compatibilidade máxima
        full_text = f"INSTRUÇÕES DE SISTEMA:\n{system_prompt}\n\n---\n\nSOLICITAÇÃO DO USUÁRIO:\n{user_prompt}"
        if json_mode:
            full_text += "\n\nResponda APENAS com o código JSON puro, sem blocos de código ou markdown."

        payload = {
            "contents": [
                {"parts": [{"text": full_text}]}
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        
        headers = {"Content-Type": "application/json"}
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                raise Exception(f"Gemini API Error {response.status_code}: {response.text}")
                
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            
            logger.info(f"✓ Gemini {model_name} (REST) | tokens={tokens}")
            return LLMResponse(text=text, model_used=f"gemini-{model_name}", tokens_used=tokens)
            
        except Exception as e:
            logger.error(f"Falha na chamada Gemini {model_name}: {e}")
            raise e

    def _call_openai(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        client = None
    ) -> LLMResponse:
        # Usa o cliente fornecido (DeepSeek) ou o padrão (OpenAI)
        target_client = client if client else self._openai_client
        
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = target_client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content.strip()
        tokens = response.usage.total_tokens
        provider = "DeepSeek" if client else "OpenAI"
        logger.info(f"✓ {provider} {model_name} | tokens={tokens}")
        return LLMResponse(text=text, model_used=f"{provider.lower()}-{model_name}", tokens_used=tokens)

    def _mock_response(self, prompt: str) -> LLMResponse:
        """Resposta mock para testes sem chamar APIs."""
        mock_json = """{
  "topic": "5 AI Tools That Will Replace Your Entire Marketing Team in 2026",
  "primary_keyword": "AI marketing tools",
  "secondary_keywords": ["AI automation", "marketing automation", "replace marketers"],
  "lsi_keywords": ["ChatGPT marketing", "Jasper AI", "Copy.ai"],
  "angle": "Contrarian: Why hiring human marketers in 2026 is a business mistake",
  "content_gap": "No one shows the ROI comparison with real numbers",
  "estimated_cpm_usd": 28.5,
  "niche_score": 91
}"""
        return LLMResponse(text=mock_json, model_used="mock", tokens_used=0)

    def generate_image(self, prompt: str, size: str = "1792x1024") -> bytes | None:
        """Gera imagem via DALL-E 3 (OpenAI) para thumbnails."""
        if not self._openai_client:
            logger.error("OpenAI client required for image generation")
            return None
        try:
            response = self._openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size=size,  # type: ignore
                quality="hd",
                n=1,
            )
            import requests as req
            image_url = response.data[0].url
            img_response = req.get(image_url, timeout=30)
            img_response.raise_for_status()
            logger.info(f"✓ DALL-E 3 image generated ({size})")
            return img_response.content
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            return None


# Singleton global
_llm_client_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = LLMClient()
    return _llm_client_instance

from shared.ai_orchestrator import orchestrator

def get_completion(prompt: str, system_instruction: str = "", model_choice: str = "gemini", json_mode: bool = False, max_tokens: int = 4096) -> str:
    """
    Função de conveniência que utiliza o AIOrchestrator (Arquiteto Tier List).
    Ignora model_choice para seguir a política de contingência 100% disponível.
    """
    try:
        response = orchestrator.chat_completion(
            system_prompt=system_instruction,
            user_prompt=prompt,
            json_mode=json_mode,
            max_tokens=max_tokens
        )
        return response.text
    except Exception as e:
        logger.error(f"❌ Falha Total no AIOrchestrator: {e}")
        return f"❌ Erro LLM: {str(e)}"
