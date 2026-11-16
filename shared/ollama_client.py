"""
Cliente REST para o servidor Ollama local (http://localhost:11434) rodando llama3.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


from shared.logger import get_logger

logger = get_logger(__name__)


class OllamaError(RuntimeError):
    """Falha de comunicação ou resposta inválida do Ollama."""


class OllamaClient:
    """Cliente HTTP REST para interagir com o Ollama local."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3")
        self.timeout = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "900"))

    def healthcheck(self) -> bool:
        """Verifica se o servidor Ollama está respondendo em http://localhost:11434."""
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return res.ok
        except (requests.ConnectionError, requests.Timeout) as exc:
            logger.info("Ollama offline ou inalcançável em %s: %s", self.base_url, exc)
            return False
        except requests.RequestException as exc:
            logger.warning("Falha de requisição HTTP no healthcheck do Ollama em %s: %s", self.base_url, exc)
            return False
        except Exception as exc:
            logger.warning("Erro durante healthcheck do Ollama em %s: %s", self.base_url, exc)
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """Retorna a lista de modelos instalados no Ollama local."""
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=10)
            res.raise_for_status()
            return res.json().get("models", [])
        except requests.RequestException as exc:
            raise OllamaError(f"Erro ao listar modelos do Ollama: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((requests.RequestException, OllamaError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        temperature: float = 0.7,
        num_predict: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Requisição REST POST para /api/generate."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "10m"),
            "options": {
                "temperature": temperature,
                "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192")),
                "num_predict": num_predict,
                "num_thread": int(os.getenv("OLLAMA_NUM_THREAD", "4")),
            },
        }
        if system_prompt:
            payload["system"] = system_prompt
        if json_mode:
            payload["format"] = "json"

        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            res_data = response.json()
            return res_data.get("response", "")
        except requests.RequestException as exc:
            raise OllamaError(f"Erro na requisição POST /api/generate do Ollama: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((requests.RequestException, OllamaError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        num_predict: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Requisição REST POST para /api/chat."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "10m"),
            "options": {
                "temperature": temperature,
                "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192")),
                "num_predict": num_predict,
                "num_thread": int(os.getenv("OLLAMA_NUM_THREAD", "4")),
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            res_data = response.json()
            return res_data.get("message", {}).get("content", "")
        except requests.RequestException as exc:
            raise OllamaError(f"Erro na requisição POST /api/chat do Ollama: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((requests.RequestException, OllamaError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        num_predict: int = 6000,
    ) -> dict[str, Any]:
        """Requisição REST POST para /api/chat com saída estruturada JSON."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        content = self.chat(messages, temperature=temperature, num_predict=num_predict, json_mode=True)
        try:
            return json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama retornou JSON inválido: {content[:300]}") from exc


def get_ollama_client() -> OllamaClient:
    return OllamaClient()
