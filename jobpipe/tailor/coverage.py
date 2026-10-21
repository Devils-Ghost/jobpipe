"""Must-have coverage of a tailored resume and the coverage.txt report."""

from __future__ import annotations

import re
from pathlib import Path

from jobpipe import facts
from jobpipe.config import settings
from jobpipe.tailor.schema import TailorOutput
from jobpipe.tailor.context import resume_text


def coverage(out: TailorOutput) -> tuple[float | None, list[str]]:
    """Must-have coverage %, plus matched keywords the resume text does not actually contain.
    None when the job names too few concrete must-haves for a percentage to mean anything."""
    kw = out.keywords
    total = len(kw.must_have_matched) + len(kw.must_have_missing)
    stems = {_stem(w) for w in re.findall(r"[a-z0-9+#./-]+", resume_text(out).lower())}
    def present(word: str) -> bool:
        return _stem(word) in stems or _stem(facts.ALIASES.get(word, word)) in stems  # golang -> go

    def years_requirement(k: str) -> bool:
        # "2+ years of ..." is checked by the experience filter before tailoring, not by text search
        return bool(re.search(r"\d+\s*\+?\s*(?:years?|yrs?)\b", k, re.I))

    absent = [k for k in kw.must_have_matched if not years_requirement(k)
              and not all(present(w) for w in re.findall(r"[a-z0-9+#./-]+", k.lower()))]
    matched = len(kw.must_have_matched) - len(absent)
    if total < settings()["tailor"].get("min_must_haves_for_coverage", 3):
        return None, absent
    return 100.0 * matched / total, absent


def _stem(w: str) -> str:
    w = w.strip("./-")
    for suffix in ("ing", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def write_coverage(folder: Path, out: TailorOutput, pct: float | None, absent: list[str]) -> None:
    kw = out.keywords
    n = len(kw.must_have_matched) + len(kw.must_have_missing)
    pct_text = (f"{pct:.0f}%" if pct is not None else
                f"not meaningful (the job names only {n} specific skill{'' if n == 1 else 's'}; "
                "judge the fit from the description)")
    lines = [
        f"Must-have coverage: {pct_text}",
        "",
        "Matched must-haves:", *[f"  + {k}" for k in kw.must_have_matched if k not in absent],
        "",
        "Missing must-haves:", *[f"  - {k}" for k in kw.must_have_missing],
    ]
    if absent:
        lines += ["", "Claimed matched but not found in the resume text:", *[f"  ? {k}" for k in absent]]
    lines += ["", "Nice-to-haves matched:", *[f"  + {k}" for k in kw.nice_matched]]
    if kw.soft_skills:
        lines += ["", "Soft skills the job asks for (for your cover letter / interview; not scored):",
                  *[f"  * {k}" for k in kw.soft_skills]]
    lines += ["", "Projects on this resume (best fit first): " + ", ".join(out.projects_used)]
    for np in out.new_projects:
        lines += ["", f"New project: {np.name} ({np.slug})", f"  why: {np.why}", f"  stack: {', '.join(np.stack)}"]
    (folder / "coverage.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
