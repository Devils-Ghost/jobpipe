"""Gemini CLI in headless mode. Prompt goes through stdin; runs in an empty temp dir."""

from __future__ import annotations

import json
from pathlib import Path

from jobpipe.llm.base import RATE_LIMIT_MARKERS, Provider, ProviderError, RateLimited, empty_workdir, run_cli

# -p is appended after stdin, so it acts as a short final instruction.
HEADLESS_SUFFIX = "Answer with only the JSON requested above. No prose, no code fences."


class Adapter(Provider):
    def run(self, prompt: str) -> str:
        # --skip-trust: the cwd is a fresh empty temp dir, so trusting it for this session is safe.
        args = [self.executable(), "-p", HEADLESS_SUFFIX, "--output-format", "json", "--skip-trust"]
        if self.config.get("model"):
            args += ["-m", self.config["model"]]
        with empty_workdir() as tmp:
            result = run_cli(args, prompt, self.timeout, Path(tmp))
        return _response_text(result.stdout)


def _response_text(stdout: str) -> str:
    """--output-format json wraps the answer as {"response": ..., "stats": ..., "error": ...}."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout  # older CLI versions print plain text
    if isinstance(payload, dict):
        err = payload.get("error")
        if err:
            msg = json.dumps(err)
            if any(m in msg.lower() for m in RATE_LIMIT_MARKERS):
                raise RateLimited(msg)
            raise ProviderError(msg)
        if "response" in payload:
            return payload["response"] or ""
    return stdout
