"""Claude Code CLI in print mode (`claude -p`), using the user's Claude subscription or API key.

Prompt goes through stdin; all tools are disabled (`--tools ""`) so it answers instead of
exploring; runs in an empty temp dir with no session saved.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

from jobpipe.llm.base import (
    RATE_LIMIT_MARKERS,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    empty_workdir,
    run_cli,
)


def _bundled_claude() -> str | None:
    """The CLI shipped inside the VS Code / Cursor extension, for people who never installed it
    on PATH. Newest extension version wins."""
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    home = Path.home()
    patterns = [
        str(home / ext / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / exe)
        for ext in (".vscode", ".vscode-insiders", ".cursor")
    ]
    found = sorted((p for pat in patterns for p in glob.glob(pat)), key=os.path.getmtime)
    return found[-1] if found else None


class Adapter(Provider):
    def executable(self) -> str:
        try:
            return super().executable()
        except ProviderUnavailable:
            bundled = _bundled_claude()
            if bundled:
                return bundled
            raise

    def run(self, prompt: str) -> str:
        args = [self.executable(), "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
        if self.config.get("model"):
            args += ["--model", self.config["model"]]
        with empty_workdir() as tmp:
            result = run_cli(args, prompt, self.timeout, Path(tmp))
        return _result_text(result.stdout)


def _result_text(stdout: str) -> str:
    """--output-format json prints one object: {"type": "result", "is_error": bool, "result": "..."}."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(payload, list):  # some versions print the whole event list
        payload = next((e for e in reversed(payload) if isinstance(e, dict) and e.get("type") == "result"), {})
    if payload.get("is_error") or payload.get("subtype", "success") != "success":
        msg = str(payload.get("result") or payload.get("subtype") or payload)[:400]
        if any(m in msg.lower() for m in RATE_LIMIT_MARKERS):
            raise RateLimited(msg)
        raise ProviderError(msg)
    text = (payload.get("result") or "").strip()
    if not text:
        raise ProviderError("claude returned an empty result")
    return text
