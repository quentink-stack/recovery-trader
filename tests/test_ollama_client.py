import json
from unittest import TestCase
from unittest.mock import patch

from recovery_trader.integrations.ollama import OllamaClient, OllamaConfig, OllamaError


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
    def test_default_local_model_configuration(self) -> None:
        config = OllamaConfig()

        self.assertEqual(config.model, "qwen3:14b")
        self.assertEqual(config.timeout, 420)
        self.assertEqual(config.temperature, 0.15)
        self.assertEqual(config.num_ctx, 8192)
        self.assertEqual(config.response_reserve, 3000)

    def test_context_budget_can_be_configured_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {"OLLAMA_NUM_CTX": "16384", "OLLAMA_RESPONSE_RESERVE": "4096"},
            clear=True,
        ):
            config = OllamaConfig.from_environment()

        self.assertEqual(config.num_ctx, 16384)
        self.assertEqual(config.response_reserve, 4096)

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
                "options": {"temperature": 0.15, "num_ctx": 8192},
                "format": "json",
            },
        )

    def test_generate_uses_a_supplied_json_schema(self) -> None:
        client = OllamaClient(OllamaConfig("http://ollama.test", "test-model", 10))
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}

        with patch("recovery_trader.integrations.ollama.urlopen", return_value=FakeResponse({"response": '{"summary": "ok"}'})) as mocked:
            client.generate("Research TEST", response_schema=schema)

        request = mocked.call_args.args[0]
        self.assertEqual(json.loads(request.data)["format"], schema)

    def test_invalid_generate_response_raises(self) -> None:
        client = OllamaClient(OllamaConfig())

        with patch("recovery_trader.integrations.ollama.urlopen", return_value=FakeResponse({"done": True})):
            with self.assertRaises(OllamaError):
                client.generate("Research TEST")

    def test_is_available_returns_false_when_server_is_unreachable(self) -> None:
        client = OllamaClient(OllamaConfig())

        with patch("recovery_trader.integrations.ollama.urlopen", side_effect=OSError("offline")):
            self.assertFalse(client.is_available())
