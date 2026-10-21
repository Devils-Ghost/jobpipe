"""Project slots: built projects, the unbuilt pool, and project headers."""

from __future__ import annotations

from typing import Any
from jobpipe import facts
from jobpipe.config import settings
from jobpipe.tailor.layout import project_bullet_count
from jobpipe.tailor.schema import Metric, NewProject, TailorOutput


def offerable_projects() -> list[dict[str, Any]]:
    """Built projects that can fill a project slot: they carry exactly the resume's number of
    curated bullets (`resume_bullets` in the fact bank)."""
    n = project_bullet_count()
    return [p for p in facts.built_projects() if len(p.get("resume_bullets") or []) == n]


def proposed_projects(pool: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in pool.get("projects", []) if p.get("status") == "proposed"]


def reusable_pool_projects(pool: dict[str, Any]) -> list[dict[str, Any]]:
    """Proposed projects whose stored bullets fit this resume's layout."""
    n = project_bullet_count()
    return [p for p in proposed_projects(pool) if len(p.get("resume_bullets") or []) == n]


def unbuilt_cap_warning(pool: dict[str, Any]) -> str | None:
    """A warning when the pool is full: no new unbuilt project can be invented (reuse still works)."""
    cap = settings()["projects"]["max_unbuilt_projects"]
    n = len(proposed_projects(pool))
    if n >= cap:
        return (f"{n} unbuilt projects already proposed (limit {cap}): no new projects will be created; "
                "existing ones can still be reused. Build some (status: built) or remove them from profile/projects_pool.yaml.")
    return None


def apply_pool_reuse(out: TailorOutput, pool: dict[str, Any]) -> None:
    """A reused pool project keeps its stored bullets unless the tailor rewrote them for the job."""
    by_slug = {p["slug"]: p for p in reusable_pool_projects(pool)}
    for np in out.new_projects:
        stored = by_slug.get(np.slug)
        if stored:
            if not np.resume_bullets:   # not rewritten for this job: keep the stored bullets
                np.resume_bullets = list(stored["resume_bullets"])
                np.metrics = [Metric(**m) for m in stored.get("metrics", [])] or np.metrics
            np.name = stored.get("name", np.name)
            np.stack = list(stored.get("stack", [])) + [s for s in np.stack if s not in stored.get("stack", [])]
            np.repo = stored.get("repo") or ""


def project_header(pid: str, new: NewProject | None) -> tuple[str, str]:
    """(title, repo URL) shown on the project's header line. The URL is "" when the fact bank
    has no GitHub account to build one from."""
    if new is not None:
        return new.name, new.repo or facts.repo_url(new.slug)
    proj = next(p for p in facts.built_projects() if p["id"] == pid)
    return facts.resume_title(proj), facts.project_repo(proj)
