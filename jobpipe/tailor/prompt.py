"""Build the tailoring prompt."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any
import yaml
from jobpipe import facts, resume
from jobpipe.config import ROOT, candidate, profile_dir, settings
from jobpipe.render.template import master_context
from jobpipe.tailor.layout import bullet_chars, project_bullet_count, project_count, projects_enabled, skill_keys, template_meta
from jobpipe.tailor.schema import TailorOutput
from jobpipe.tailor.slots import offerable_projects, proposed_projects, reusable_pool_projects


def _style_text() -> str:
    """profile/style.md if the person has their own rules, else prompts/style.md."""
    own = profile_dir() / "style.md"
    return (own if own.exists() else ROOT / "prompts" / "style.md").read_text(encoding="utf-8")


def style_prompt() -> str:
    """The style rules as prose plus one compact line of everything the lint bans."""
    text = _style_text()
    rules = lint_rules()
    banned = [*rules.get("banned_words", []), *rules.get("banned_phrases", []), *rules.get("banned_qualifiers", [])]
    return text.split("The lists below")[0].split("```yaml")[0].rstrip() + "\n\nBanned: " + ", ".join(banned)


def lint_rules() -> dict[str, Any]:
    return _lint_rules(_style_text())


@lru_cache(maxsize=4)
def _lint_rules(text: str) -> dict[str, Any]:
    m = re.search(r"```yaml\n(.*?)```", text, re.S)
    return yaml.safe_load(m.group(1)) if m else {}


def _bold_markup(segs: list[dict[str, Any]]) -> str:
    return "".join(f"**{s['t']}**" if s["b"] and s["t"].strip() else s["t"] for s in segs).replace("****", "")


def experience_bullets() -> dict[str, list[str]]:
    """The master resume's experience bullets, verbatim (**bold** markup), keyed by the role's
    header line as it appears on the resume."""
    ctx = master_context(resume.active().master)
    return {e["header"]: [_bold_markup(b) for b in e["bullets"]] for e in ctx["experience"]}


def experience_text() -> str:
    lines = []
    for header, bullets in experience_bullets().items():
        lines.append(f"{header}:")
        lines += [f"  - {b}" for b in bullets]
    return "\n".join(lines) or "(no experience section on this resume)"


def build_prompt(jd_text: str, pool: dict[str, Any]) -> str:
    proposed = proposed_projects(pool)
    cap = settings()["projects"]["max_unbuilt_projects"]
    max_new = settings()["projects"]["max_new_per_resume"]
    if not projects_enabled():
        rule = "New projects are turned off: new_projects must be []."
    elif len(proposed) >= cap:
        rule = (f"Do NOT invent a new project: the pool already holds {len(proposed)} unbuilt projects "
                f"(limit {cap}). You may still reuse one from the pool.")
    else:
        rule = ""
    labels = template_meta()["skill_labels"]
    skill_rows = ", ".join(f"`{k}` ({labels[k].strip().rstrip(':') or 'unlabeled row'})" for k in skill_keys())
    pool_view = [
        {k: p.get(k) for k in ("slug", "name", "stack", "resume_bullets") if p.get(k)}
        for p in reusable_pool_projects(pool)
    ]
    view = facts.llm_view()
    offer = {p["id"] for p in offerable_projects()}
    view["projects"] = [p for p in view["projects"] if p["id"] in offer]
    lo, hi = bullet_chars()
    tpl = (ROOT / "prompts" / "tailor.md").read_text(encoding="utf-8")
    repl = {
        "<<JD>>": jd_text[:30000],
        "<<FACTS>>": yaml.safe_dump(view, sort_keys=False, allow_unicode=True, width=120),
        "<<POOL>>": yaml.safe_dump(pool_view, sort_keys=False, allow_unicode=True) if pool_view else "(empty)",
        "<<EXPERIENCE>>": experience_text(),
        "<<STYLE>>": style_prompt(),
        "<<PROJECT_COUNT>>": str(project_count()),
        "<<PROJECT_BULLETS>>": str(project_bullet_count()),
        "<<MAX_NEW>>": str(max_new),
        "<<BULLET_MIN>>": str(lo),
        "<<BULLET_MAX>>": str(hi),
        "<<NEW_PROJECT_RULE>>": rule,
        "<<CANDIDATE>>": candidate()["name"],
        "<<FIELD>>": candidate()["field"],
        "<<GITHUB>>": facts.github_user(),
        "<<SKILL_ROWS>>": skill_rows,
        "<<SKILLS_SKELETON>>": json.dumps({k: [] for k in skill_keys()}),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def retry_prompt(prompt: str, previous: TailorOutput, errors: list[str]) -> str:
    return (
        prompt
        + "\n\n# Your previous answer broke these rules. Fix every one and return the complete JSON again.\n"
        + "\n".join(f"- {e}" for e in errors)
        + "\n\nPrevious answer:\n"
        + previous.model_dump_json()
    )
