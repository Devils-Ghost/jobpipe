"""Role-based LLM routing: `complete(role, prompt, schema)` tries each provider in order."""

from __future__ import annotations

import datetime as dt
import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import TypeAdapter, ValidationError

from jobpipe.config import providers_config
from jobpipe.llm.base import Provider, ProviderError, ProviderUnavailable, extract_json

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    value: Any
    provider: str
    elapsed_s: float
    raw: str
    attempts: list[dict[str, Any]] = field(default_factory=list)


class AllProvidersFailed(Exception):
    def __init__(self, role: str, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "no providers configured"
        super().__init__(f"all providers failed for role {role!r}: {detail}")


def provider_enabled(cfg: dict[str, Any], today: dt.date | None = None) -> bool:
    until = cfg.get("enabled_until")
    if until is None:
        return True
    if isinstance(until, str):
        until = dt.date.fromisoformat(until)
    return (today or dt.date.today()) <= until


def load_provider(name: str, cfg: dict[str, Any]) -> Provider:
    module = importlib.import_module(f"jobpipe.llm.{cfg['adapter']}")
    return module.Adapter(name, cfg)


_override: str | None = None


def force_provider(name: str | None) -> None:
    """Route every role to one provider (used by `tailor --provider` for comparisons)."""
    global _override
    if name is not None and name not in providers_config()["providers"]:
        raise KeyError(f"unknown provider {name!r}; known: {sorted(providers_config()['providers'])}")
    _override = name


def providers_for(role: str) -> list[tuple[str, dict[str, Any]]]:
    conf = providers_config()
    if _override:
        return [(_override, conf["providers"][_override])]
    names = conf.get("roles", {}).get(role)
    if not names:
        raise KeyError(f"no providers configured for role {role!r}")
    return [(n, conf["providers"][n]) for n in names]


def complete(
    role: str,
    prompt: str,
    schema: Any = None,
    *,
    check: Callable[[Any], None] | None = None,
    today: dt.date | None = None,
) -> LLMResult:
    """Run `prompt` for `role`. `schema` is a pydantic model or any type TypeAdapter accepts
    (e.g. list[Job]); None returns raw text. `check` may raise ValueError for extra
    validation (it counts as a validation failure and falls through)."""
    adapter = TypeAdapter(schema) if schema is not None else None
    attempts: list[dict[str, Any]] = []
    for name, cfg in providers_for(role):
        if not provider_enabled(cfg, today):
            attempts.append({"provider": name, "error": f"disabled after {cfg.get('enabled_until')}"})
            continue
        start = time.monotonic()
        try:
            provider = load_provider(name, cfg)
            raw = provider.run(prompt)
            if adapter is None:
                value: Any = raw
            else:
                value = adapter.validate_python(extract_json(raw))
                if check:
                    check(value)
        except (ProviderError, ValidationError, ValueError) as e:
            kind = type(e).__name__
            attempts.append({"provider": name, "error": f"{kind}: {_short(e)}", "elapsed_s": _since(start)})
            log.warning("provider %s failed for %s: %s", name, role, kind)
            continue
        elapsed = _since(start)
        attempts.append({"provider": name, "ok": True, "elapsed_s": elapsed})
        log.info("role %s answered by %s in %.1fs", role, name, elapsed)
        return LLMResult(value=value, provider=name, elapsed_s=elapsed, raw=raw, attempts=attempts)
    raise AllProvidersFailed(role, attempts)


def _since(start: float) -> float:
    return round(time.monotonic() - start, 2)


def _short(e: Exception, n: int = 400) -> str:
    s = str(e).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "..."


__all__ = ["AllProvidersFailed", "LLMResult", "ProviderUnavailable", "complete", "provider_enabled"]
