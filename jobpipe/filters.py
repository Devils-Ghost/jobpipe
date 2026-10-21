"""Skip rules. Each returns a reason string when the job should be skipped, else None."""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz

from jobpipe.config import path, settings

# ---------------------------------------------------------------- sponsorship

NO_SPONSOR_PATTERNS = [
    r"(?:not|unable to|cannot|can ?not|won'?t|will not|do(?:es)? not|are not able to)\s+(?:currently\s+)?(?:offer|provide|sponsor|support)\w*\s+(?:any\s+)?(?:\w+\s+){0,3}?(?:visa\s+)?sponsorship",
    r"(?:not|unable to|cannot|can ?not|won'?t|will not|do(?:es)? not)\s+(?:currently\s+)?sponsor",
    r"no\s+(?:visa\s+)?sponsorship",
    r"sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided|supported)",
    r"without\s+(?:the\s+)?need\s+(?:for|of)\s+(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship",
    r"without\s+(?:requiring\s+)?(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship",
    r"must\s+(?:be\s+)?(?:legally\s+)?authorized\s+to\s+work\s+in\s+the\s+(?:u\.?s\.?|united states)\s+without\s+(?:\w+\s+){0,3}sponsorship",
    r"(?:must|required to)\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen",
    r"(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?(?:required|is a requirement)",
    r"(?:requires?|required)\s+(?:u\.?s\.?|united states)\s+citizenship",
    r"only\s+(?:u\.?s\.?|united states)\s+citizens",
    r"(?:active|current|ability to obtain(?: and maintain)?|must (?:have|hold|possess|obtain))\s+(?:an?\s+)?(?:(?:active|dod|government|secret|top secret|ts/sci|public trust)\s+)*(?:security\s+)?clearance",
    r"\b(?:ts/sci|top secret|secret)\s+(?:security\s+)?clearance\b",
    r"\bgreen card holders?\s+(?:or|and)\s+(?:u\.?s\.?\s+)?citizens\s+only\b",
    r"\b(?:gc|green card)\s*(?:/|or)\s*(?:usc|u\.?s\.?\s*citizens?)\s+only\b",
    r"\bno\s+h-?1b\b",
    r"\bh-?1b\s+(?:visa\s+)?(?:sponsorship\s+)?(?:is\s+)?(?:not\s+(?:available|offered|provided|supported)|unavailable)\b",
    r"\b(?:does not|doesn'?t|will not|won'?t|cannot|can'?t|unable to)\s+sponsor\b",
]
_NO_SPONSOR = [re.compile(p, re.I) for p in NO_SPONSOR_PATTERNS]


def sponsorship_reason(text: str, llm_note: str = "") -> str | None:
    """Regex over the JD first, then over the LLM's sponsorship note. Off unless
    settings.filters.require_sponsorship is true (the person needs visa sponsorship)."""
    if not settings()["filters"].get("require_sponsorship", False):
        return None
    for source, blob in (("jd", text), ("llm note", llm_note)):
        for pat in _NO_SPONSOR:
            m = pat.search(blob or "")
            if m:
                return f"sponsorship ({source}): {_snippet(blob, m)}"
    return None


# ---------------------------------------------------------------- consultancy / staffing


def staffing_reason(text: str) -> str | None:
    for sig in settings()["filters"]["staffing_signals"]:
        m = re.search(rf"(?<![\w-]){re.escape(sig)}(?![\w-])", text or "", re.I)
        if m:
            return f"staffing signal: {_snippet(text, m)}"
    return None


@lru_cache
def denylist(file: Path | None = None) -> tuple[str, ...]:
    p = file or path("denylist")
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return tuple(ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#"))


def _company_norm(name: str) -> str:
    n = name.lower()
    n = re.sub(r"[.,]", " ", n)
    n = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|technologies|technology|solutions|group|services)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def consultancy_reason(company: str, employer_type: str, jd_text: str = "", names: Iterable[str] | None = None) -> str | None:
    excluded = settings()["filters"]["exclude_employer_types"]
    if employer_type in excluded:
        return f"employer type: {employer_type}"
    cn = _company_norm(company)
    for name in names if names is not None else denylist():
        dn = _company_norm(name)
        if dn and (cn == dn or (len(dn) >= 4 and fuzz.token_set_ratio(cn, dn) >= 95)):
            return f"denylist: {name}"
    return staffing_reason(jd_text)


# ---------------------------------------------------------------- fit for the candidate


def experience_reason(required_years: float | None) -> str | None:
    limit = settings()["filters"]["max_required_years"]
    if required_years is not None and required_years > limit:
        return f"experience: requires {required_years:g}+ years (limit {limit})"
    return None


def _year_month(s: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", s or "")
    return (int(m[1]), int(m[2])) if m and 1 <= int(m[2]) <= 12 else None


def graduation_reason(earliest: str = "", latest: str = "", requires_enrollment: bool = False,
                      note: str = "", graduated: str | None = None, today: dt.date | None = None) -> str | None:
    """Skip jobs whose graduation window excludes the candidate's graduation month
    (settings.filters.graduation_date, "YYYY-MM"), or that require current enrollment after the
    candidate has graduated. Off when graduation_date is not set."""
    grad = _year_month(graduated if graduated is not None else settings()["filters"].get("graduation_date"))
    if not grad:
        return None
    today = today or dt.date.today()
    mine = f"{grad[0]}-{grad[1]:02d}"
    quote = f": {note[:120]}" if note else ""
    lo, hi = _year_month(earliest), _year_month(latest)
    if lo and grad < lo:
        return f"graduation: must graduate {earliest} or later (graduated {mine}){quote}"
    if hi and grad > hi:
        return f"graduation: must graduate by {latest} (graduated {mine}){quote}"
    if requires_enrollment and grad < (today.year, today.month):
        return f"graduation: requires current enrollment (graduated {mine}){quote}"
    return None


def relevance_reason(role_relevant: bool, why: str = "") -> str | None:
    return None if role_relevant else f"not a relevant role: {why or 'description is not software engineering'}"


# ---------------------------------------------------------------- reposted / processed / applied


REPOSTED = re.compile(r"\breposted\b", re.I)


def reposted_reason(text: str, flag: bool = False) -> str | None:
    if flag:
        return "reposted (listing flag)"
    m = REPOSTED.search(text or "")
    return f"reposted: {_snippet(text, m)}" if m else None


def processed_reason(job_key: str, applications: Path | None = None) -> str | None:
    """A folder ending in _<job_key> anywhere under applications/ means it was done."""
    root = applications or path("applications")
    if not root.exists():
        return None
    for d in root.glob(f"*/*_{job_key}"):
        if d.is_dir():
            return f"already processed: {d.relative_to(root)}"
    return None


def applied_reason(company: str, title: str, rows: Iterable[tuple], threshold: int | None = None,
                   today: dt.date | None = None) -> str | None:
    """rows: (tab, company, title[, date applied]). Fuzzy match on company and title separately.
    A row applied more than filters.reapply_after_days ago no longer blocks; a row with no
    date always blocks."""
    threshold = threshold or settings()["filters"]["applied_fuzzy_threshold"]
    reapply = settings()["filters"].get("reapply_after_days")
    today = today or dt.date.today()
    cn = _company_norm(company)
    for row in rows:
        tab, rc, rt = row[:3]
        applied = row[3] if len(row) > 3 else None
        if fuzz.token_sort_ratio(cn, _company_norm(rc)) >= threshold and fuzz.token_sort_ratio(title.lower(), rt.lower()) >= threshold:
            if reapply and applied and (today - applied).days > reapply:
                continue  # old enough to apply again
            when = f" on {applied:%Y-%m-%d}" if applied else ""
            return f"already applied ({tab}{when}): {rc} / {rt}"
    return None


def _names_company(company: str, name: str) -> bool:
    """Whole-word, case-insensitive: "Amazon" matches "Amazon Web Services", not "Amazonia"."""
    en = _company_norm(name)
    return bool(en) and f" {en} " in f" {_company_norm(company)} "


def excluded_reason(company: str, exclude: Iterable[str]) -> str | None:
    """--exclude: skip a company whose name contains any listed name as whole words."""
    for name in exclude:
        if _names_company(company, name):
            return f"excluded (--exclude {name})"
    return None


def blacklisted_reason(company: str, blacklist: Iterable[tuple[str, str]]) -> str | None:
    """The tracker's blacklist (company, reason): always skipped, whatever the role or date."""
    for name, why in blacklist:
        if _names_company(company, name):
            return f"blacklisted (sheet): {name}" + (f": {why}" if why else "")
    return None


def _snippet(text: str, m: re.Match, pad: int = 50) -> str:
    s = text[max(0, m.start() - pad) : m.end() + pad]
    return " ".join(s.split())
