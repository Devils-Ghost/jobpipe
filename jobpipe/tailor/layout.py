"""What the active resume template offers: skill rows, project slots, bullet size."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from jobpipe import resume
from jobpipe.config import settings


def template_meta() -> dict[str, Any]:
    return _read_json(str(resume.active().meta_file), resume.active().meta_file.stat().st_mtime)


@lru_cache(maxsize=16)
def _read_json(p: str, _mtime: float) -> dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def skill_keys() -> list[str]:
    return template_meta()["skill_keys"]


def project_count() -> int:
    return len(template_meta()["project_bullet_counts"])


def project_bullet_count() -> int:
    counts = template_meta()["project_bullet_counts"]
    return counts[0] if counts else 0


def projects_enabled() -> bool:
    """New projects are proposed only if enabled and the resume has a projects section."""
    return bool(settings()["projects"].get("enabled", True)) and project_count() > 0


def bullet_chars() -> tuple[int, int]:
    """Allowed length of a written project bullet, in characters (about two lines)."""
    lo, hi = settings()["tailor"]["bullet_chars"]
    return int(lo), int(hi)
