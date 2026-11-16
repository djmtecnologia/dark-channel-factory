"""
Testes unitários para o OllamaClient REST.
Compatível com unittest e pytest.
"""
import unittest
from unittest.mock import MagicMock, patch

from shared.ollama_client import OllamaClient, OllamaError, get_ollama_client


class TestOllamaClient(unittest.TestCase):

    def test_ollama_client_init_defaults(self):
        client = get_ollama_client()
        self.assertEqual(client.base_url, "http://localhost:11434")
        self.assertEqual(client.model, "llama3")

    @patch("requests.get")
    def test_healthcheck_success(self, mock_get):
        mock_get.return_value.ok = True
        client = OllamaClient()
        self.assertTrue(client.healthcheck())
        mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)

    @patch("requests.get")
    def test_healthcheck_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        client = OllamaClient()
        self.assertFalse(client.healthcheck())

    @patch("requests.post")
    def test_generate_rest(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"response": "Resposta do Llama3 via REST"}
        mock_post.return_value = mock_response

        client = OllamaClient(base_url="http://localhost:11434", model="llama3")
        res = client.generate(prompt="Olá Ollama", temperature=0.5)

        self.assertEqual(res, "Resposta do Llama3 via REST")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/generate")
        self.assertEqual(kwargs["json"]["model"], "llama3")
        self.assertEqual(kwargs["json"]["prompt"], "Olá Ollama")

    @patch("requests.post")
    def test_chat_json_rest(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"message": {"content": '{"status": "ok", "mensagem": "sucesso"}'}}
        mock_post.return_value = mock_response

        client = OllamaClient(base_url="http://localhost:11434", model="llama3")
        res = client.chat_json(system_prompt="Você é um assistente", user_prompt="Responda em JSON")

        self.assertEqual(res, {"status": "ok", "mensagem": "sucesso"})
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/chat")
        self.assertEqual(kwargs["json"]["model"], "llama3")
        self.assertEqual(kwargs["json"]["format"], "json")


if __name__ == "__main__":
    unittest.main()
