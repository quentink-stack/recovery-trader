"""Client for a locally running Ollama server."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout: int = 120

    @classmethod
    def from_environment(cls) -> "OllamaConfig":
        return cls(os.getenv("OLLAMA_BASE_URL", cls.base_url).rstrip("/"), os.getenv("OLLAMA_MODEL", cls.model), int(os.getenv("OLLAMA_TIMEOUT", str(cls.timeout))))


class OllamaError(RuntimeError):
    """Raised when Ollama cannot be reached or returns an invalid response."""


class OllamaClient:
    def __init__(self, config: OllamaConfig | None = None) -> None:
        self.config = config or OllamaConfig.from_environment()

    def _get_json(self, path: str, payload: dict | None = None) -> dict | list:
        request = Request(f"{self.config.base_url}{path}", data=json.dumps(payload).encode("utf-8") if payload is not None else None, headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST" if payload is not None else "GET")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

    def is_available(self) -> bool:
        try:
            self._get_json("/api/tags")
        except OllamaError:
            return False
        return True

    def generate(self, prompt: str, *, json_response: bool = True) -> str:
        payload: dict[str, str | bool] = {"model": self.config.model, "prompt": prompt, "stream": False}
        if json_response:
            payload["format"] = "json"
        response = self._get_json("/api/generate", payload)
        if not isinstance(response, dict) or not isinstance(response.get("response"), str):
            raise OllamaError("Ollama returned an invalid generate response.")
        return response["response"]
