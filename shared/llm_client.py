"""
LLM Client — Dark Channel Factory
Gerencia cascata: Gemini 2.5 Flash → Gemini 1.5 Flash → OpenAI GPT-4o-mini
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
GEMINI_PRIMARY = os.getenv("LLM_PRIMARY_MODEL", "gemini-2.5-flash-preview-04-17")
GEMINI_FALLBACK = os.getenv("LLM_FALLBACK_MODEL", "gemini-1.5-flash")
OPENAI_FALLBACK = os.getenv("LLM_FINAL_FALLBACK", "gpt-4o-mini")
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
        self._init_clients()

    def _init_clients(self) -> None:
        """Inicializa clientes disponíveis silenciosamente."""
        # Gemini
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key and gemini_key != "your_gemini_api_key_here":
            try:
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=gemini_key)
                self._gemini_client = genai
                logger.debug(f"Gemini client initialized (primary: {GEMINI_PRIMARY})")
            except ImportError:
                logger.warning("google-generativeai not installed — Gemini unavailable")

        # OpenAI
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key and openai_key != "your_openai_api_key_here":
            try:
                from openai import OpenAI  # type: ignore
                self._openai_client = OpenAI(api_key=openai_key)
                logger.debug("OpenAI client initialized (fallback)")
            except ImportError:
                logger.warning("openai not installed — OpenAI fallback unavailable")

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

        # Tentativa 2: Gemini Fallback
        if self._gemini_client:
            try:
                return self._call_gemini(
                    GEMINI_FALLBACK, system_prompt, user_prompt, temperature, max_tokens, json_mode
                )
            except Exception as e:
                logger.warning(f"Gemini fallback ({GEMINI_FALLBACK}) failed: {e}")

        # Tentativa 3: OpenAI
        if self._openai_client:
            try:
                return self._call_openai(
                    OPENAI_FALLBACK, system_prompt, user_prompt, temperature, max_tokens, json_mode
                )
            except Exception as e:
                logger.error(f"OpenAI fallback also failed: {e}")
                raise RuntimeError("All LLM providers failed") from e

        raise RuntimeError(
            "No LLM clients available. Check GEMINI_API_KEY or OPENAI_API_KEY in .env"
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
        import google.generativeai as genai  # type: ignore

        gen_config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if json_mode:
            gen_config["response_mime_type"] = "application/json"

        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config=genai.types.GenerationConfig(**gen_config),
        )
        response = model.generate_content(user_prompt)
        text = response.text.strip()
        tokens = getattr(response.usage_metadata, "total_token_count", 0)
        logger.info(f"✓ Gemini {model_name} | tokens={tokens}")
        return LLMResponse(text=text, model_used=model_name, tokens_used=tokens)

    def _call_openai(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
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

        response = self._openai_client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content.strip()
        tokens = response.usage.total_tokens
        logger.info(f"✓ OpenAI {model_name} | tokens={tokens}")
        return LLMResponse(text=text, model_used=model_name, tokens_used=tokens)

    def _mock_response(self, prompt: str) -> LLMResponse:
        """Resposta mock para testes sem chamar APIs."""
        mock_json = """{
  "topic": "5 AI Tools That Will Replace Your Entire Marketing Team in 2025",
  "primary_keyword": "AI marketing tools",
  "secondary_keywords": ["AI automation", "marketing automation", "replace marketers"],
  "lsi_keywords": ["ChatGPT marketing", "Jasper AI", "Copy.ai"],
  "angle": "Contrarian: Why hiring human marketers in 2025 is a business mistake",
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
