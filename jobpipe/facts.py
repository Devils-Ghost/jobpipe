"""Fact bank access: the filtered view the LLM sees, project headers, and the skills whitelist."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from jobpipe.config import path

def load() -> dict[str, Any]:
    p = path("fact_bank")
    return _load_yaml_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=8)
def _load_yaml_cached(p: str, _mtime: float) -> dict[str, Any]:
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_pool() -> dict[str, Any]:
    with open(path("projects_pool"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {"projects": []}


def built_projects() -> list[dict[str, Any]]:
    """Built projects that may appear in the Projects section."""
    from jobpipe.config import settings

    excluded = set(settings()["tailor"].get("exclude_projects", []))
    return [p for p in load()["projects"] if p.get("status") == "built" and p["id"] not in excluded]


def llm_view() -> dict[str, Any]:
    """What the tailor prompt sees about projects and skills (no contact details)."""
    projects = [
        {k: p[k] for k in ("id", "name", "tools", "resume_bullets") if p.get(k)} for p in built_projects()
    ]
    return {"projects": projects, "allowed_skills": sorted(allowed_skills())}


# ---------------------------------------------------------------- project headers


def github_user() -> str:
    """Account name from contact.github (https://github.com/<user>), or "" when not set."""
    url = (load().get("contact") or {}).get("github") or ""
    m = re.match(r"https?://github\.com/([^/\s]+)", url)
    return m.group(1) if m else ""


def repo_url(slug: str) -> str:
    """Where a project's repo lives (or will live: unbuilt projects point at a private repo the
    owner makes public once it is built). "" without a GitHub account in the fact bank."""
    user = github_user()
    return f"https://github.com/{user}/{slug}" if user else ""


def repo_label(url: str) -> str:
    """'https://github.com/owner/name' -> 'owner/name', the text shown on the resume."""
    return re.sub(r"^https?://github\.com/", "", url).strip("/")


def project_repo(proj: dict[str, Any]) -> str:
    """A built project's repo URL: its `repo` field, else <github account>/<id without 'proj-'>."""
    return proj.get("repo") or repo_url(re.sub(r"^proj-", "", proj["id"]))


def resume_title(proj: dict[str, Any]) -> str:
    """Heading on the resume: `resume_title`, else the name without a trailing '(...)'."""
    return proj.get("resume_title") or re.sub(r"\s*\(.*?\)", "", proj["name"]).strip()


# ---------------------------------------------------------------- skills whitelist


def norm_skill(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def master_skill_items() -> tuple[str, ...]:
    """Items on the master resume's skills rows (user decision: all of them are allowed)."""
    from jobpipe import resume
    from jobpipe.render.template import master_context

    items = []
    for segs in master_context(resume.active().master)["skills"].values():
        text = "".join(s["t"] for s in segs)
        items += [x.strip() for x in text.split(",") if x.strip()]
    return tuple(items)


def allowed_skills(extra: list[str] | None = None) -> set[str]:
    """fact_bank skills.hands_on + every master resume skill + optional project stacks."""
    hands_on = load()["skills"]["hands_on"]
    out = {s for group in hands_on.values() for s in group}
    out |= set(master_skill_items())
    out |= set(load()["skills"].get("core", []))
    if extra:
        out |= set(extra)
    return out


def skill_allowed(item: str, allowed_norm: set[str]) -> bool:
    """Exact (case-insensitive) match, or every part of a slash/ampersand compound matches
    ("Gitlab/Github", "C/C++", "Linux/Unix"), or a parenthetical-stripped match."""
    n = norm_skill(item)
    if n in allowed_norm:
        return True
    squash = lambda t: re.sub(r"[\s.\-_]", "", t)   # "Nodejs" = "Node.js"
    if squash(n) in {squash(a) for a in allowed_norm}:
        return True
    stripped = norm_skill(re.sub(r"\(.*?\)", "", item))
    if stripped and stripped in allowed_norm:
        return True
    parts = [p for p in re.split(r"\s*[/&]\s*", n) if p]
    return len(parts) > 1 and all(p in allowed_norm or _alias(p) in allowed_norm for p in parts)


ALIASES = {
    "golang": "go",
    "js": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "linux": "unix/linux",
    "unix": "unix/linux",
    "gitlab": "gitlab ci",
    "spring": "spring boot",
    "rest apis": "rest",
    "restful apis": "rest",
    "azure cloud": "azure",
    "microsoft azure": "azure",
}


def _alias(n: str) -> str:
    return ALIASES.get(n, n)


def allowed_norm(extra: list[str] | None = None) -> set[str]:
    base = {norm_skill(s) for s in allowed_skills(extra)}
    # every alias whose target is allowed is allowed too
    base |= {a for a, target in ALIASES.items() if target in base}
    return base
