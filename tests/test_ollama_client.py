import json
from unittest import TestCase
from unittest.mock import patch

from ollama_client import OllamaClient, OllamaConfig, OllamaError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class OllamaClientTests(TestCase):
    def test_default_timeout_allows_five_minutes_for_local_generation(self) -> None:
        self.assertEqual(OllamaConfig().timeout, 300)
        self.assertEqual(OllamaConfig().temperature, 0.15)

    def test_generate_uses_configured_model_and_json_format(self) -> None:
        client = OllamaClient(OllamaConfig("http://ollama.test", "test-model", 10))

        with patch("recovery_trader.integrations.ollama.urlopen", return_value=FakeResponse({"response": '{"score": 72}'})) as mocked:
            response = client.generate("Research TEST")

        self.assertEqual(response, "{\"score\": 72}")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://ollama.test/api/generate")
        self.assertEqual(
            json.loads(request.data),
            {
                "model": "test-model",
                "prompt": "Research TEST",
                "stream": False,
                "options": {"temperature": 0.15},
                "format": "json",
            },
        )

    def test_invalid_generate_response_raises(self) -> None:
        client = OllamaClient(OllamaConfig())

        with patch("recovery_trader.integrations.ollama.urlopen", return_value=FakeResponse({"done": True})):
            with self.assertRaises(OllamaError):
                client.generate("Research TEST")

    def test_is_available_returns_false_when_server_is_unreachable(self) -> None:
        client = OllamaClient(OllamaConfig())

        with patch("recovery_trader.integrations.ollama.urlopen", side_effect=OSError("offline")):
            self.assertFalse(client.is_available())
