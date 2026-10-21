"""Job page -> JD text, job metadata, and the employer apply link (exact href from the page)."""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from jobpipe.scrape.browser import Snapshot, browser_context, is_jobright, snapshot_page
from jobpipe.config import ROOT, settings
from jobpipe.llm import complete

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 30000
MAX_LINKS = 400

EmployerType = Literal["direct", "staffing_agency", "it_consultancy", "big_consulting", "unknown"]


class NotAJobPage(Exception):
    pass


class JobPageMeta(BaseModel):
    is_job_posting: bool = True
    company: str = ""
    title: str = ""
    location: str = ""
    apply_link_index: int = -1
    reposted: bool = False
    sponsorship_note: str = ""
    employer_type: EmployerType = "unknown"
    employer_type_reason: str = ""
    required_years: float | None = None
    grad_earliest: str = ""        # "YYYY-MM": candidates must graduate on or after this
    grad_latest: str = ""          # "YYYY-MM": candidates must graduate on or before this
    requires_enrollment: bool = False
    grad_note: str = ""
    role_relevant: bool = True
    relevance_reason: str = ""
    resume_id: str = ""


@dataclass
class JobPage:
    url: str
    text: str
    meta: JobPageMeta
    apply_url: str = ""
    provider: str = ""
    attempts: list[dict] = field(default_factory=list)
    text_source: str = "page"      # "original" (employer posting), "jobright", or "page" (the URL given)
    original_url: str = ""         # the posting page the text came from, when text_source == "original"
    source_note: str = ""          # why the original posting was not used, if it was not


def _links_block(snap: Snapshot) -> str:
    return "\n".join(f"[{i}] {a.text[:80]} -> {a.href}" for i, a in enumerate(snap.anchors[:MAX_LINKS]))


def _resumes_block() -> str:
    from jobpipe import resume

    rs = resume.all_resumes()
    if len(rs) <= 1:
        return f'"{rs[0].id}" (the only resume)' if rs else '"" (no resumes yet)'
    return "\n  ".join(f'- "{r.id}": {r.description or "no description"}' for r in rs)


def analyze(snap: Snapshot) -> JobPage:
    tpl = (ROOT / "prompts" / "job_page.md").read_text(encoding="utf-8")
    prompt = (
        tpl.replace("<<TEXT>>", snap.text[:MAX_TEXT_CHARS])
        .replace("<<LINKS>>", _links_block(snap))
        .replace("<<TARGET_ROLES>>", " ".join(settings()["filters"]["target_roles"].split()))
        .replace("<<RESUMES>>", _resumes_block())
    )

    def check(meta: JobPageMeta) -> None:
        if not meta.is_job_posting:
            return
        if not meta.company or not meta.title:
            raise ValueError("company and title are required")
        if meta.apply_link_index >= min(len(snap.anchors), MAX_LINKS):
            raise ValueError(f"apply_link_index {meta.apply_link_index} out of range")

    res = complete("extract", prompt, JobPageMeta, check=check)
    meta: JobPageMeta = res.value
    if not meta.is_job_posting:
        raise NotAJobPage(f"no job posting at {snap.url} (page title: {snap.title!r}); the job may be closed")
    apply_url = snap.anchors[meta.apply_link_index].href if meta.apply_link_index >= 0 else ""
    # Exact-match guarantee: the URL is taken from the page's own anchor list, never from the model.
    assert not apply_url or apply_url in snap.hrefs()
    return JobPage(url=snap.url, text=snap.text, meta=meta, apply_url=apply_url,
                   provider=res.provider, attempts=res.attempts)


# ---------------------------------------------------------------- original posting

_ASHBY_APP = re.compile(r"^(https://jobs\.ashbyhq\.com/[^?#]+?)/application/?(\?.*)?$")
_LEVER_APPLY = re.compile(r"^(https://jobs\.lever\.co/[^?#]+?)/apply/?(\?.*)?$")
_WORKDAY_APPLY = re.compile(r"^(https://[^/]+\.myworkdayjobs\.com/.+?)/apply(?:/[^?#]*)?(\?.*)?$")
_JD_CUES = ("responsibilit", "qualification", "requirement", "what you'll", "what you will", "you will",
            "about the role", "about you", "experience", "skills")


def posting_url(apply_url: str) -> str:
    """The job description page behind an apply link. Several ATSs link straight to the
    application form, which has no description: map those back to the posting."""
    u = apply_url.strip()
    for pat in (_ASHBY_APP, _LEVER_APPLY, _WORKDAY_APPLY):
        m = pat.match(u)
        if m:
            return m.group(1) + (m.group(2) or "")
    parsed = urlparse(u)
    if parsed.hostname in ("job-boards.greenhouse.io", "boards.greenhouse.io") and parsed.path.startswith("/embed/job_app"):
        q = parse_qs(parsed.query)
        if q.get("for") and q.get("token"):
            return f"https://job-boards.greenhouse.io/{q['for'][0]}/jobs/{q['token'][0]}"
    return u


def looks_like_jd(text: str) -> bool:
    """A real job description: long enough and talks about the role, not just a form or login wall."""
    t = text.lower()
    return len(text) >= 1200 and sum(cue in t for cue in _JD_CUES) >= 3


JOBRIGHT_NOISE_START = re.compile(r"^\s*Insider Connection", re.I | re.M)
JOBRIGHT_SECTION = re.compile(r"^\s*(Responsibilities|Qualification|Required|Preferred|Benefits|Company)\s*$", re.M)


def strip_jobright_noise(text: str) -> str:
    """Drop Jobright's people panel ("Insider Connection": names of employees, alumni, emails)."""
    m = JOBRIGHT_NOISE_START.search(text)
    if not m:
        return text
    end = JOBRIGHT_SECTION.search(text, m.end())
    return text[: m.start()] + (text[end.start():] if end else "")


_SMARTRECRUITERS = re.compile(r"^https://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)/(\d+)")


def _html_to_text(fragment: str) -> str:
    text = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def smartrecruiters_text(data: dict) -> str:
    """Posting JSON from SmartRecruiters' public API -> plain text with section headings."""
    loc = data.get("location") or {}
    parts = [data.get("name", ""), ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)]
    for sec in (data.get("jobAd") or {}).get("sections", {}).values():
        if sec.get("text"):
            parts.append(f"\n{sec.get('title', '')}\n{_html_to_text(sec['text'])}")
    return "\n".join(p for p in parts if p)


def ats_api_text(url: str) -> tuple[str, str] | None:
    """(text, api_url) for job boards whose pages render nothing to a headless browser but
    publish postings through a public API. Currently SmartRecruiters."""
    m = _SMARTRECRUITERS.match(url)
    if not m:
        return None
    api = f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}"
    with urllib.request.urlopen(api, timeout=30) as r:
        return smartrecruiters_text(json.load(r)), api


def use_original_posting(job: JobPage, page) -> JobPage:
    """Replace a Jobright page's text with the employer's original posting when it can be read
    and looks like a job description; otherwise keep Jobright's text minus its people panel."""
    job.text = strip_jobright_noise(job.text)
    job.text_source = "jobright"
    if not job.apply_url:
        job.source_note = "no original posting link on the Jobright page"
        return job
    url = posting_url(job.apply_url)
    try:
        api = ats_api_text(url)
    except Exception as e:  # noqa: BLE001  (fall through to loading the page)
        log.info("ATS API failed for %s: %s", url, e)
        api = None
    if api and looks_like_jd(api[0]):
        job.text, job.text_source, job.original_url = api[0], "original", url
        return job
    try:
        snap = snapshot_page(page, url)
    except Exception as e:  # noqa: BLE001  (fall back to Jobright's copy)
        job.source_note = f"original posting could not be loaded: {type(e).__name__}"
        return job
    if not looks_like_jd(snap.text):
        job.source_note = f"original posting did not look like a job description ({len(snap.text)} chars)"
        return job
    job.text, job.text_source, job.original_url = snap.text, "original", snap.url
    return job


def fetch(url: str) -> JobPage:
    """Read one job URL. A Jobright URL is analyzed on Jobright, then its text is replaced by the
    original posting when possible. An employer URL that points at an application form is first
    mapped to its posting page."""
    jobright = is_jobright(url)
    target = url if jobright else posting_url(url)
    with browser_context(persistent=jobright) as ctx:
        page = ctx.new_page()
        snap = snapshot_page(page, target)
        if not snap.text.strip():
            raise ValueError(f"page has no visible text: {target}")
        job = analyze(snap)
        if jobright:
            job.url = url
            use_original_posting(job, page)
        elif not job.apply_url:
            job.apply_url = url  # an employer page is itself the place to apply
    return job
