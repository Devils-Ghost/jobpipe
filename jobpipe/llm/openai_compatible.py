"""Any OpenAI-compatible chat-completions endpoint over plain HTTP: OpenAI, OpenRouter, Groq,
Together, LM Studio, Ollama, vLLM, and similar.

providers.yaml examples:
  openai:
    adapter: openai_compatible
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY        # name of the variable in .env holding the key
    model: <model name from the provider>
  ollama:
    adapter: openai_compatible
    base_url: http://localhost:11434/v1
    api_key_env: null                  # local servers need no key
    model: <a model you have pulled, e.g. via `ollama pull`>
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from jobpipe.config import env
from jobpipe.llm.base import Provider, ProviderError, ProviderTimeout, ProviderUnavailable, RateLimited

SYSTEM = "You produce exactly the output format requested, with no commentary."


class Adapter(Provider):
    def executable(self) -> str:
        if not self.config.get("base_url") or not self.config.get("model"):
            raise ProviderUnavailable(f"{self.name}: set base_url and model in providers.yaml")
        key_env = self.config.get("api_key_env")
        if key_env and not env(key_env):
            raise ProviderUnavailable(f"{self.name}: {key_env} is not set in .env")
        return self.config["base_url"]

    def run(self, prompt: str) -> str:
        base = self.executable().rstrip("/")
        body = {
            "model": self.config["model"],
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        }
        if self.config.get("temperature") is not None:
            body["temperature"] = self.config["temperature"]
        headers = {"Content-Type": "application/json"}
        if self.config.get("api_key_env"):
            headers["Authorization"] = f"Bearer {env(self.config['api_key_env'])}"
        req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429:
                raise RateLimited(f"{self.name} rate limited: {detail}") from e
            raise ProviderError(f"{self.name} HTTP {e.code}: {detail}") from e
        except (socket.timeout, TimeoutError) as e:
            raise ProviderTimeout(f"{self.name} timed out after {self.timeout}s") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"{self.name} unreachable: {e.reason}") from e
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: unexpected response {str(payload)[:200]}") from e
        if not text.strip():
            raise ProviderError(f"{self.name} returned an empty answer")
        return text
