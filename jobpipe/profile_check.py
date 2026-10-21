"""`jobpipe check-profile`: catch fact-bank and profile mistakes before a run does."""

from __future__ import annotations

from typing import Any

from jobpipe import facts, resume
from jobpipe.config import candidate, path, profile_dir

STATUSES = {"verified", "no_resume", "confirm"}
PROJECT_STATUSES = {"built", "proposed", "building", "design_only"}


def check_profile() -> tuple[list[str], list[str]]:
    """(problems, notes). Problems block good results; notes are informational."""
    problems: list[str] = []
    notes: list[str] = []
    if not profile_dir().exists():
        return [f"no profile folder at {profile_dir()} (run the setup in SETUP.md)"], notes
    if candidate()["name"] == "Candidate":
        problems.append("settings.yaml: set candidate.name (used in resume file names and prompts)")

    if not path("fact_bank").exists():
        return problems + [f"missing {path('fact_bank')}"], notes
    fb: dict[str, Any] = facts.load()
    for key in ("contact", "skills", "projects"):
        if key not in fb:
            problems.append(f"fact_bank.yaml: missing top-level '{key}'")
    contact = fb.get("contact") or {}
    for k in ("name", "email"):
        if not contact.get(k):
            problems.append(f"fact_bank.yaml: contact.{k} is empty (used to keep personal details out of test fixtures)")
    hands_on = (fb.get("skills") or {}).get("hands_on") or {}
    if not any(hands_on.values()):
        problems.append("fact_bank.yaml: skills.hands_on has no skills (the skills whitelist would be only the resume's own rows)")

    ids: dict[str, str] = {}
    for role in fb.get("experience") or []:
        for f in role.get("facts") or []:
            _check_id(f.get("id"), f"experience {role.get('id')}", ids, problems)
            if f.get("status", "verified") not in STATUSES:
                problems.append(f"fact {f.get('id')}: status must be one of {sorted(STATUSES)}")
    for p in fb.get("projects") or []:
        _check_id(p.get("id"), "projects", ids, problems)
        if p.get("status") not in PROJECT_STATUSES:
            problems.append(f"project {p.get('id')}: status must be one of {sorted(PROJECT_STATUSES)}")
        if p.get("status") == "built" and not (p.get("facts") or p.get("tools")):
            problems.append(f"project {p.get('id')}: a built project needs facts (what/metrics) and tools")

    resumes = resume.all_resumes()
    if not resumes:
        problems.append(f"no master resume: add {path('resumes')}/<id>/master_resume.docx")
    for r in resumes:
        if not r.template.exists():
            problems.append(f"resume {r.id}: template not built (run `jobpipe build-template`)")
            continue
        if len(resumes) > 1 and not r.description:
            problems.append(f"resume {r.id}: add a description in resume.yaml so jobs can pick the right resume")
        with resume.use(r):
            from jobpipe.tailor import offerable_projects, project_bullet_count, style_lint, template_meta

            notes.append(f"resume {r.id}: skills rows {template_meta()['skill_keys']}, "
                         f"{len(_master_context_projects(r))} project slot(s) of {project_bullet_count()} bullets")
            offered = {p["id"] for p in offerable_projects()}
            for p in fb.get("projects") or []:
                if p.get("status") != "built" or p["id"] in offered or not project_bullet_count():
                    continue
                notes.append(f"resume {r.id}: built project {p['id']} is not offered: it has {len(p.get('resume_bullets') or [])} "
                             f"resume_bullets, the resume needs {project_bullet_count()}")
            for p in offerable_projects():
                for b in p["resume_bullets"]:
                    for issue in style_lint(b):
                        problems.append(f"project {p['id']} bullet {b[:40]!r}...: {issue}")
            if offered:
                notes.append(f"resume {r.id}: {len(offered)} built project(s) can fill a slot: {', '.join(sorted(offered))}")
    if not facts.github_user():
        notes.append("fact_bank.yaml: contact.github is empty, so project headers show no repo link")
    return problems, notes


def _master_context_projects(r: resume.Resume) -> list[dict[str, Any]]:
    from jobpipe.render.template import master_context

    return master_context(r.master)["projects"]


def _check_id(fid: str | None, where: str, seen: dict[str, str], problems: list[str]) -> None:
    if not fid:
        problems.append(f"{where}: an entry has no id")
    elif fid in seen:
        problems.append(f"duplicate id {fid!r} ({seen[fid]} and {where})")
    else:
        seen[fid] = where
