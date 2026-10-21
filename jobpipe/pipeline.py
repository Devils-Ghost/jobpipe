"""Per-job work shared by `tailor` and `run`: tailor, validate, render, fit, save."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jobpipe import facts, filters, resume
from jobpipe.config import candidate, path, settings
from jobpipe.scrape.jd import JobPage
from jobpipe.llm import complete
from jobpipe.render import fit_one_page
from jobpipe.tailor.coverage import coverage, write_coverage
from jobpipe.tailor import (
    TailorOutput,
    Validation,
    apply_pool_reuse,
    build_context,
    build_prompt,
    unbuilt_cap_warning,
    fit_skill_rows,
    retry_prompt,
    drop_unlisted_skills,
    validate,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- naming


def job_key(url: str) -> str:
    """Stable id for a job: Jobright's id when available, else a short hash of the URL."""
    u = urlparse(url)
    if (u.hostname or "").endswith("jobright.ai"):
        seg = [s for s in u.path.split("/") if s]
        if seg:
            return re.sub(r"[^A-Za-z0-9]", "", seg[-1])[:32]
    return "u" + hashlib.sha1(url.encode()).hexdigest()[:10]


def safe(s: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:n].strip("-") or "Unknown"


def folder_for(day: dt.date, company: str, title: str, key: str) -> Path:
    return path("applications") / day.isoformat() / f"{safe(company, 30)}_{safe(title, 50)}_{key}"


# ---------------------------------------------------------------- result


@dataclass
class JobResult:
    company: str
    title: str
    location: str
    apply_url: str
    source_url: str
    folder: str
    coverage_pct: float | None = None
    new_project: str = ""
    flags: list[str] = field(default_factory=list)
    pdf: str = ""
    resume: str = ""

    def csv_row(self) -> dict[str, Any]:
        return {
            "company": self.company, "role": self.title, "location": self.location,
            "apply_url": self.apply_url, "jobright_url": self.source_url if "jobright.ai" in self.source_url else "",
            "folder": self.folder, "resume": self.resume,
            "must_have_coverage": "n/a (few specific skills)" if self.coverage_pct is None else f"{self.coverage_pct:.0f}%",
            "new_project": self.new_project, "flags": "; ".join(self.flags),
        }


# ---------------------------------------------------------------- tailor


def run_tailor(jd_text: str, log_data: dict[str, Any]) -> tuple[TailorOutput, Validation]:
    """Tailor with one retry on rule violations. Returns the last output and its validation."""
    pool = facts.load_pool()
    prompt = build_prompt(jd_text, pool)
    calls = []
    out = val = None
    for attempt in range(1 + int(log_data.get("retries_allowed", 1))):
        p = prompt if out is None else retry_prompt(prompt, out, val.errors)
        res = complete("tailor", p, TailorOutput)
        out = res.value
        apply_pool_reuse(out, pool)
        val = validate(out, pool)
        calls.append({"attempt": attempt + 1, "provider": res.provider, "elapsed_s": res.elapsed_s,
                      "provider_attempts": res.attempts, "errors": val.errors, "warnings": val.warnings})
        if val.ok:
            break
    if not val.ok and any(e.startswith("skills.") and "not in the fact bank" in e for e in val.errors):
        dropped = drop_unlisted_skills(out)
        val = validate(out, pool)
        val.warnings.append(f"dropped skills outside the whitelist: {dropped}")
    log_data["tailor_calls"] = calls
    return out, val


def _source_line(job: JobPage) -> str:
    if job.text_source == "original":
        return f"original posting ({job.original_url})"
    if job.text_source == "jobright":
        return f"Jobright's copy ({job.source_note})"
    return job.url


def write_jd(folder: Path, job: JobPage) -> None:
    m = job.meta
    header = [
        f"# {m.title} at {m.company}",
        "",
        f"- Location: {m.location}",
        f"- Source: {job.url}",
        f"- Apply: {job.apply_url or 'n/a'}",
        f"- Description source: {_source_line(job)}",
        f"- Employer type: {m.employer_type} ({m.employer_type_reason})",
        f"- Sponsorship note: {m.sponsorship_note or 'none on page'}",
        f"- Reposted: {m.reposted}",
        "",
        "---",
        "",
    ]
    (folder / "jd.md").write_text("\n".join(header) + job.text.strip() + "\n", encoding="utf-8")


def process_job(job: JobPage, day: dt.date, key: str | None = None, resume_id: str | None = None) -> JobResult:
    """Steps 6-9 for one job whose page is already fetched and analyzed, using the forced
    resume, else the one the job-page reader picked, else the default."""
    r = resume.choose(job.meta.resume_id, resume_id)
    with resume.use(r):
        res = _process_job(job, day, key)
    res.resume = r.id
    return res


def _process_job(job: JobPage, day: dt.date, key: str | None) -> JobResult:
    started = time.monotonic()
    m = job.meta
    key = key or job_key(job.url)
    folder = folder_for(day, m.company, m.title, key)
    folder.mkdir(parents=True, exist_ok=True)
    result = JobResult(m.company, m.title, m.location, job.apply_url, job.url, str(folder.relative_to(path("applications"))))
    log_data: dict[str, Any] = {
        "url": job.url, "job_key": key, "started": dt.datetime.now().isoformat(timespec="seconds"),
        "job_page": {"provider": job.provider, "attempts": job.attempts},
        "jd_source": {"source": job.text_source, "original_url": job.original_url, "note": job.source_note,
                      "chars": len(job.text)},
        "retries_allowed": settings()["tailor"]["retries"],
    }
    write_jd(folder, job)

    # checks that `run` uses to skip; `tailor` reports them as flags instead
    for reason in (
        filters.reposted_reason(job.text, m.reposted),
        filters.sponsorship_reason(job.text, m.sponsorship_note),
        filters.consultancy_reason(m.company, m.employer_type, job.text),
        filters.experience_reason(m.required_years),
        filters.graduation_reason(m.grad_earliest, m.grad_latest, m.requires_enrollment, m.grad_note),
        filters.relevance_reason(m.role_relevant, m.relevance_reason),
    ):
        if reason:
            result.flags.append(reason)

    cap_warning = unbuilt_cap_warning(facts.load_pool())
    if cap_warning:
        result.flags.append(f"project cap reached: {cap_warning.split(':')[0]}")
    out, val = run_tailor(job.text, log_data)
    if not val.ok:
        result.flags.append(f"validation: {len(val.errors)} rule violation(s) after retry, review tailor.json")
    trimmed = fit_skill_rows(out)
    if trimmed:
        val.warnings.append(f"skills trimmed to keep each row on one line: {trimmed}")
    pct, absent = coverage(out)
    result.coverage_pct = pct
    result.new_project = ", ".join(np.slug for np in out.new_projects)
    (folder / "tailor.json").write_text(
        json.dumps({"output": out.model_dump(), "validation": asdict(val)}, indent=2), encoding="utf-8"
    )
    write_coverage(folder, out, pct, absent)

    base = f"{safe(candidate()['name'], 40).replace('-', '_')}_{safe(m.company, 30)}"
    docx, pdf = folder / f"{base}.docx", folder / f"{base}.pdf"
    fit = fit_one_page(build_context(out), docx, pdf)
    if not fit.fits:
        result.flags.append(f"overflow: {fit.pages} pages after full fit ladder")

    if out.new_projects:
        from jobpipe.projects import handle_new_projects, outcomes_json

        outcomes = handle_new_projects(out.new_projects, folder, result.folder)
        log_data["new_projects"] = outcomes_json(outcomes)
        for o in outcomes:
            if o.folder and not o.error:
                result.new_project = f"{o.slug} ({o.folder}{', new' if o.created else ''})"
            if o.error:
                result.flags.append(f"new project {o.slug}: {o.error}")
    result.pdf = str(pdf)

    log_data.update(
        {
            "fit": asdict(fit),
            "lint": val.lint,
            "validation_errors": val.errors,
            "validation_warnings": val.warnings,
            "flags": result.flags,
            "elapsed_s": round(time.monotonic() - started, 1),
        }
    )
    (folder / "log.json").write_text(json.dumps(log_data, indent=2, default=str), encoding="utf-8")
    return result
