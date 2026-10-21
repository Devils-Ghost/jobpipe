"""Settings and paths.

Tool defaults live in config/. Everything about the person lives in the profile folder
(default `profile/`, override with the JOBPIPE_PROFILE environment variable):

  profile/settings.yaml    overrides for config/settings.yaml (deep-merged, optional)
  profile/providers.yaml   overrides for config/providers.yaml (deep-merged, optional)
  profile/fact_bank.yaml   facts about the person
  profile/projects_pool.yaml
  profile/resumes/<id>/    one folder per master resume (see jobpipe/resume.py)
"""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Files that belong to the person, resolved inside the profile folder.
PROFILE_FILES = {
    "fact_bank": "fact_bank.yaml",
    "projects_pool": "projects_pool.yaml",
    "resumes": "resumes",
}


def profile_dir() -> Path:
    p = Path(os.environ.get("JOBPIPE_PROFILE") or (ROOT / "profile"))
    return p if p.is_absolute() else (ROOT / p)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Dicts merge key by key; any other value (lists included) in `override` replaces."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


@lru_cache
def settings() -> dict[str, Any]:
    return deep_merge(_load_yaml(ROOT / "config" / "settings.yaml"), _load_yaml(profile_dir() / "settings.yaml"))


@lru_cache
def providers_config() -> dict[str, Any]:
    return deep_merge(_load_yaml(ROOT / "config" / "providers.yaml"), _load_yaml(profile_dir() / "providers.yaml"))


def reload() -> None:
    """Forget cached settings (tests, or after the profile changed on disk)."""
    settings.cache_clear()
    providers_config.cache_clear()


def path(key: str) -> Path:
    """Absolute path for a profile file (fact_bank, projects_pool, resumes) or a key under
    settings.paths (applications, browser_profile, denylist), which are relative to the repo."""
    if key in PROFILE_FILES:
        return profile_dir() / PROFILE_FILES[key]
    p = Path(settings()["paths"][key])
    return p if p.is_absolute() else ROOT / p


def candidate() -> dict[str, Any]:
    """settings.candidate: name (file names, prompts) and field (e.g. "software engineering")."""
    c = settings().get("candidate") or {}
    return {"name": c.get("name") or "Candidate", "field": c.get("field") or "software engineering"}


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value or default
