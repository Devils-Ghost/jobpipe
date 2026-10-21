"""Provider interface plus the subprocess and JSON helpers every CLI adapter shares.

Adding a provider: create jobpipe/llm/<name>.py defining a class `Adapter(Provider)`,
then add an entry with `adapter: <name>` to config/providers.yaml.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ProviderError(Exception):
    """Any failure that should make the router fall through to the next provider."""


class ProviderTimeout(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    """CLI missing, not logged in, or disabled by date."""


RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "resource_exhausted", "quota exceeded", "too many requests")


class Provider(ABC):
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.timeout = int(config.get("timeout_seconds", 300))

    @abstractmethod
    def run(self, prompt: str) -> str:
        """Send the prompt, return the model's text answer. Raise ProviderError on failure."""

    def executable(self) -> str:
        command = self.config.get("command", self.name)
        exe = shutil.which(command)
        if not exe:
            raise ProviderUnavailable(f"{command!r} is not on PATH")
        return exe


def run_cli(args: list[str], stdin_text: str, timeout: int, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a CLI with the prompt on stdin. Kills the whole process tree on timeout
    (npm shims on Windows spawn node as a child, which a plain kill would orphan)."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise ProviderTimeout(f"{Path(args[0]).name} timed out after {timeout}s")
    result = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if proc.returncode != 0:
        blob = f"{out}\n{err}".lower()
        if any(m in blob for m in RATE_LIMIT_MARKERS):
            raise RateLimited(_tail(err or out))
        raise ProviderError(f"exit {proc.returncode}: {_tail(err or out)}")
    return result


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    try:
        proc.communicate(timeout=10)
    except Exception:
        pass


def _tail(text: str, n: int = 600) -> str:
    text = (text or "").strip()
    return text[-n:]


def empty_workdir() -> tempfile.TemporaryDirectory[str]:
    """A fresh empty directory so the CLI agent cannot read the repo."""
    return tempfile.TemporaryDirectory(prefix="jobpipe-llm-")


_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n?```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON from a model answer: strips code fences and surrounding chatter."""
    text = text.strip()
    candidates = [m.group(1).strip() for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
    # Fall back to the outermost {...} or [...] span.
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if starts:
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as e:
                raise ValueError(f"no parseable JSON in model output: {e}") from e
    raise ValueError("no JSON found in model output")
