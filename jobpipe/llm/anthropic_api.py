"""Claude via the Anthropic API (official `anthropic` SDK). Pay-per-use: needs ANTHROPIC_API_KEY
in .env, or an `ant auth login` profile.

providers.yaml example:
  anthropic_api:
    adapter: anthropic_api
    model: claude-opus-5-5      # or claude-sonnet-5-5 / claude-haiku-5-5 for lower cost
    effort: medium              # low | medium | high | xhigh | max
    timeout_seconds: 300
"""

from __future__ import annotations

from jobpipe.llm.base import Provider, ProviderError, ProviderTimeout, ProviderUnavailable, RateLimited

DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept the server-side refusal fallback ("default" routing).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
SYSTEM = "You produce exactly the output format requested, with no commentary."


class Adapter(Provider):
    def executable(self) -> str:
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("the anthropic package is not installed (uv add anthropic)") from e
        return "anthropic-sdk"

    def run(self, prompt: str) -> str:
        import anthropic

        self.executable()
        model = self.config.get("model") or DEFAULT_MODEL
        client = anthropic.Anthropic(timeout=float(self.timeout), max_retries=2)
        kwargs = dict(
            model=model,
            max_tokens=int(self.config.get("max_tokens", 16000)),
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        if self.config.get("effort"):
            kwargs["output_config"] = {"effort": self.config["effort"]}
        try:
            if model in FALLBACK_MODELS and self.config.get("server_fallback", True):
                # On a safety decline the API retries on a fallback model within the same call.
                response = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                response = client.messages.create(**kwargs)
        except anthropic.APITimeoutError as e:
            raise ProviderTimeout(f"anthropic API timed out after {self.timeout}s") from e
        except anthropic.RateLimitError as e:
            raise RateLimited(f"anthropic API rate limited: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise ProviderUnavailable("anthropic API key missing or invalid (ANTHROPIC_API_KEY in .env)") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"anthropic API {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic API connection error: {e}") from e

        if response.stop_reason == "refusal":
            raise ProviderError("anthropic API declined the request (stop_reason: refusal)")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise ProviderError(f"anthropic API returned no text (stop_reason: {response.stop_reason})")
        return text
