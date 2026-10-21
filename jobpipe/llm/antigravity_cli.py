"""Antigravity CLI (`agy`), authenticated with the Google AI Pro subscription.

The prompt goes in through stdin as a single stream-json user event (agy ignores plain
piped text, and long prompts exceed the Windows command-line limit). Runs in an empty
temp dir: workspace file writes are auto-allowed in headless mode but there is nothing
there to touch, and shell commands are soft-denied by default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jobpipe.llm.base import (
    RATE_LIMIT_MARKERS,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    empty_workdir,
    run_cli,
)

# agy is an agent with web search enabled by default. Left alone it researches the
# company instead of answering, and runs out of time with an empty response.
NO_TOOLS = "Do not call tools (no search, browsing, files or shell). Answer only from this message.\n\n"

AUTH_MARKERS =("authentication required", "not authenticated", "sign in", "oauth-callback", "accounts.google.com")


class Adapter(Provider):
    def executable(self) -> str:
        try:
            return super().executable()
        except ProviderUnavailable:
            # Default install location; not on PATH in terminals opened before install.
            default = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
            if default.exists():
                return str(default)
            raise

    def run(self, prompt: str) -> str:
        args = [
            self.executable(),
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--print-timeout", f"{self.timeout}s",
        ]
        if self.config.get("model"):
            args += ["--model", self.config["model"]]
        event = json.dumps({"event": "user", "message": {"content": NO_TOOLS + prompt}}, ensure_ascii=False)
        with empty_workdir() as tmp:
            # agy enforces --print-timeout itself; the outer timeout is a backstop.
            try:
                result = run_cli(args, event + "\n", self.timeout + 60, Path(tmp))
            except RateLimited:
                raise
            except ProviderError as e:
                if any(m in str(e).lower() for m in AUTH_MARKERS):
                    raise ProviderError("agy is not signed in: run `agy` once in a terminal and sign in") from e
                raise
        return _response_text(result.stdout, result.stderr)


def _response_text(stdout: str, stderr: str = "") -> str:
    final = _result_event(stdout)
    if final is None:
        raise ProviderError(f"agy emitted no result event; stderr: {stderr.strip()[-300:]}")
    status = final.get("status")
    if status != "SUCCESS":
        msg = f"{status}: {final.get('error') or stderr.strip()[-300:]}"
        low = msg.lower()
        if any(m in low for m in RATE_LIMIT_MARKERS):
            raise RateLimited(msg)
        if any(m in low for m in AUTH_MARKERS):
            raise ProviderError(f"agy is not signed in (run `agy` once interactively): {msg}")
        raise ProviderError(msg)
    text = (final.get("response") or "").strip()
    if not text:
        raise ProviderError("agy returned an empty response")
    return text


def _result_event(stdout: str) -> dict[str, Any] | None:
    """Last NDJSON object carrying the result envelope (status + response)."""
    found = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # The envelope may sit at the top level or nested under the event payload.
        for candidate in (obj, obj.get("result"), obj.get("data")):
            if isinstance(candidate, dict) and "status" in candidate and "response" in candidate:
                found = candidate
    return found
