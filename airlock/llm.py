from __future__ import annotations

import ipaddress
import json
import time
import urllib.request
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlparse

from .config import ModelConfig


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    seconds: float
    tokens: int


class LLMClient(Protocol):
    def complete(self, prompt: str) -> Completion: ...


def require_loopback(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(f"LLM endpoint must be loopback, got: {url}")


class OllamaClient:
    """Talks only to a local Ollama server."""

    def __init__(self, cfg: ModelConfig):
        require_loopback(cfg.url)
        self.cfg = cfg

    def with_model(self, name: str) -> OllamaClient:
        return OllamaClient(replace(self.cfg, name=name))

    def names(self) -> list[str]:
        """Model tags installed in the local Ollama."""
        return sorted(m["name"] for m in self._request("/api/tags", timeout_s=5).get("models", []))

    def unload(self) -> None:
        self._post({"model": self.cfg.name, "keep_alive": 0})

    def warm_up(self) -> None:
        """Load the model into memory with the same options real requests use."""
        self._post({"model": self.cfg.name, "keep_alive": self.cfg.keep_alive,
                    "options": {"num_ctx": self.cfg.num_ctx}})

    def _request(self, path: str, body: dict | None = None, timeout_s: float | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.cfg.url}{path}", data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s or self.cfg.timeout_s) as resp:
            return json.load(resp)

    def _post(self, body: dict) -> dict:
        return self._request("/api/generate", body)

    def complete(self, prompt: str) -> Completion:
        body = {
            "model": self.cfg.name,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx,
                        "num_predict": self.cfg.num_predict},
        }
        start = time.monotonic()
        data = self._post(body)
        return Completion(data.get("response", ""), self.cfg.name,
                          time.monotonic() - start, data.get("eval_count", 0))
