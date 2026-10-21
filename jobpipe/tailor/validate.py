"""Code-enforced rules for the model's answer."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from jobpipe import facts
from jobpipe.config import settings
from jobpipe.tailor.layout import bullet_chars, project_bullet_count, project_count, projects_enabled, skill_keys
from jobpipe.tailor.prompt import experience_bullets, lint_rules
from jobpipe.tailor.schema import TailorOutput
from jobpipe.tailor.slots import offerable_projects, proposed_projects, reusable_pool_projects
from jobpipe.tailor.text import numbers, plain


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def all_bullet_texts(out: TailorOutput) -> list[str]:
    """Bullets the tailor wrote: those of new projects. Everything else is fixed text."""
    return [t for np in out.new_projects for t in np.resume_bullets]


def built_bullets(out: TailorOutput) -> list[str]:
    """Curated bullets of the built projects on this resume."""
    by_id = {p["id"]: p for p in offerable_projects()}
    return [b for pid in out.projects_used if pid in by_id for b in by_id[pid]["resume_bullets"]]


def fixed_texts(out: TailorOutput) -> list[str]:
    """Text on the resume that the tailor did not write: experience + built projects' bullets."""
    return [t for bs in experience_bullets().values() for t in bs] + built_bullets(out)


def style_lint(text: str) -> list[str]:
    rules = lint_rules()
    t = plain(text)
    problems = []
    for ch in rules.get("banned_chars", []):
        if ch in t:
            problems.append(f"contains {ch!r}")
    for w in rules.get("banned_words", []):
        if w == "i":
            hit = re.search(r"(?<![\w/-])I(?![\w/-])", t)
        elif w == "us":
            hit = re.search(r"(?<![\w-])us(?![\w-])", t)  # lowercase only; "US" is a country
        else:
            hit = re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", t, re.I)
        if hit:
            problems.append(f"banned word {w!r}")
    for q in rules.get("banned_qualifiers", []):
        if re.search(rf"(?<![\w-]){re.escape(q)}(?![\w-])", t, re.I):
            problems.append(f"AI-jargon qualifier {q!r}")
    for ph in rules.get("banned_phrases", []):
        if re.search(rf"(?<![\w-]){re.escape(ph)}(?![\w-])", t, re.I):
            problems.append(f"banned phrase {ph!r}")
    return problems


STOPWORDS = set(
    "with using through across into from that which their them while within over each more than also such both "
    "based this these those for and the via per after before under during when where while what were have has had "
    "been being they then them other only into onto".split()
)


def repeated_terms(texts: list[str], limit: int) -> dict[str, int]:
    words = Counter()
    for t in texts:
        for w in re.findall(r"[A-Za-z][A-Za-z+#.\-]*[A-Za-z+#]|[A-Za-z]", plain(t)):
            lw = w.lower()
            if len(lw) >= 4 and lw not in STOPWORDS:
                words[lw] += 1
    return {w: n for w, n in words.items() if n > limit}


def validate(out: TailorOutput, pool: dict[str, Any]) -> Validation:
    v = Validation()
    n_proj, n_pb = project_count(), project_bullet_count()
    built = {p["id"]: p for p in offerable_projects()}
    new_by_slug = {np.slug: np for np in out.new_projects}
    pool_slugs = {p["slug"] for p in pool.get("projects", [])}

    # projects: every slot is a built project (fixed curated bullets) or a new/pool project
    if len(out.projects_used) != n_proj or len(set(out.projects_used)) != len(out.projects_used):
        v.errors.append(f"projects_used must list exactly {n_proj} distinct ids; got {out.projects_used}")
    for pid in out.projects_used:
        if pid not in built and pid not in new_by_slug:
            v.errors.append(f"projects_used has {pid!r}, which is neither a built project id nor a new_projects slug")

    # new projects
    cfg = settings()["projects"]
    if len(out.new_projects) > cfg["max_new_per_resume"]:
        v.errors.append(f"at most {cfg['max_new_per_resume']} new projects per resume; got {len(out.new_projects)}")
    if out.new_projects and not projects_enabled():
        v.errors.append("new projects are turned off (settings.projects.enabled, or the resume has no projects section); new_projects must be []")
    unbuilt = len(proposed_projects(pool))
    lo, hi = bullet_chars()
    pool_bullets = {p["slug"]: list(p["resume_bullets"]) for p in reusable_pool_projects(pool)}
    stored = {np.slug for np in out.new_projects if np.resume_bullets == pool_bullets.get(np.slug)}   # unedited pool bullets
    for np in out.new_projects:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", np.slug):
            v.errors.append(f"new project slug {np.slug!r} must be lowercase kebab-case")
        if np.slug not in pool_slugs and unbuilt >= cfg["max_unbuilt_projects"]:
            v.errors.append(
                f"new project {np.slug!r} not allowed: {unbuilt} unbuilt projects already proposed "
                f"(limit {cfg['max_unbuilt_projects']}); reuse one from the pool or drop it"
            )
        if np.slug not in out.projects_used:
            v.errors.append(f"new project {np.slug!r} must also be listed in projects_used")
        if len(np.resume_bullets) != n_pb:
            v.errors.append(f"new project {np.slug!r}: needs exactly {n_pb} resume_bullets, got {len(np.resume_bullets)}")
        if np.slug in stored:
            continue
        claimed = set().union(*(numbers(m.claim) for m in np.metrics)) if np.metrics else set()
        tech = _tech_terms() | {facts.norm_skill(s) for s in np.stack}
        for i, t in enumerate(np.resume_bullets, 1):
            label = f"new project {np.slug!r} bullet {i}"
            if not numbers(t):
                v.errors.append(f"{label}: has no metric; every bullet needs at least one number")
            missing = numbers(t) - claimed - {"0"}   # "zero duplicates" needs no separate claim
            if missing:
                v.errors.append(f"{label}: numbers {sorted(missing)} are not in its metrics claims")
            size = len(plain(t))
            if not lo <= size <= hi:
                gap = lo - size if size < lo else size - hi
                v.errors.append(f"{label}: {size} characters without ** markers; {'add' if size < lo else 'cut'} at least {gap} "
                                f"(allowed {lo}-{hi}): {plain(t)}")
            for problem in xyz_problems(t, None, tech):
                v.errors.append(f"{label}: {problem}")

    # skills
    extra = [t for pid in out.projects_used if pid in built for t in built[pid].get("tools", [])]
    extra += [t for np in out.new_projects for t in np.stack]
    allowed = facts.allowed_norm(extra)
    keys = skill_keys()
    missing_rows = [k for k in keys if k not in out.skills]
    if missing_rows:
        v.errors.append(f"skills is missing rows {missing_rows}; use exactly these keys: {keys}")
    for row in [k for k in out.skills if k not in keys]:
        v.warnings.append(f"skills row {row!r} is not on the resume; ignored")
        out.skills.pop(row)
    for key in keys:
        items = out.skills.get(key, [])
        if not items:
            v.errors.append(f"skills.{key} is empty")
        bad = [s for s in items if not facts.skill_allowed(s, allowed)]
        if bad:
            v.errors.append(f"skills.{key} has items not in the fact bank or a used project's stack: {bad}")

    # style and repetition (only the bullets the tailor wrote can be changed)
    texts = [t for np in out.new_projects if np.slug not in stored for t in np.resume_bullets]
    reused = [t for np in out.new_projects if np.slug in stored for t in np.resume_bullets]
    for t in texts:
        for problem in style_lint(t):
            v.lint.append(f"{problem}: {plain(t)[:70]}...")
    v.errors += [f"style: {x}" for x in v.lint]
    written = {w for t in texts for w in re.findall(r"[a-z][a-z+#.\-]*[a-z+#]", plain(t).lower())}
    limit = settings()["tailor"]["max_term_repeats"]
    reps = {w: n for w, n in repeated_terms(fixed_texts(out) + reused + texts, limit).items() if w in written}
    if reps:
        v.errors.append(
            f"terms used more than {limit} times across bullets: "
            + ", ".join(f"{w} x{n}" for w, n in sorted(reps.items(), key=lambda x: -x[1]))
        )
    return v


XYZ_CONNECTOR = re.compile(r"(?<![\w-])(by|through|using|via|leveraging|with)(?![\w-])", re.I)


def xyz_problems(text: str, cited_numbers: set[str] | None, tech: set[str]) -> list[str]:
    """Checks for Google's XYZ shape: a metric when the facts have one (Y), a 'by/through/
    using' clause (Z), and at least one named technology."""
    problems = []
    t = plain(text)
    if cited_numbers and not (numbers(t) & cited_numbers):
        problems.append(f"XYZ: cited facts have metrics {sorted(cited_numbers)} but the bullet uses none")
    if not XYZ_CONNECTOR.search(t):
        problems.append("XYZ: no 'by/through/using/via' clause saying how it was done")
    low = t.lower()
    if not any(re.search(rf"(?<![\w-]){re.escape(s)}(?![\w])", low) for s in tech):
        problems.append("XYZ: names no technology from the fact bank or skills list")
    return problems


def _tech_terms() -> set[str]:
    terms = {facts.norm_skill(s) for s in facts.allowed_skills()}
    for role in facts.load()["experience"]:
        for f in role.get("facts", []):
            terms |= {facts.norm_skill(t) for t in f.get("tools", [])}
    for p in facts.load()["projects"]:
        terms |= {facts.norm_skill(t) for t in p.get("tools", [])}
    # split compounds so "SSL/TLS" also yields "ssl" and "tls"
    terms |= {part for s in list(terms) for part in re.split(r"\s*/\s*", s) if len(part) > 1}
    return {t for t in terms if t}


def drop_unlisted_skills(out: TailorOutput) -> list[str]:
    """Remove skills the whitelist rejects (last resort after the retry); returns what was dropped."""
    built = {p["id"]: p for p in facts.built_projects()}
    extra = [t for pid in out.projects_used if pid in built for t in built[pid].get("tools", [])]
    extra += [t for np in out.new_projects for t in np.stack]
    allowed = facts.allowed_norm(extra)
    dropped = []
    for key, items in out.skills.items():
        keep = [s for s in items if facts.skill_allowed(s, allowed)]
        dropped += [s for s in items if s not in keep]
        out.skills[key] = keep
    return dropped
