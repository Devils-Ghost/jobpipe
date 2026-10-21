"""Recommended page -> job cards, via the LLM, with exact-match URL validation in code."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from jobpipe.scrape.browser import Snapshot
from jobpipe.config import ROOT
from jobpipe.scrape.jd import EmployerType
from jobpipe.llm import complete

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 60000
MAX_LINKS = 600


class JobCard(BaseModel):
    company: str
    title: str
    location: str = ""
    jobright_url: str
    reposted: bool = False
    sponsorship_note: str = ""
    employer_type: EmployerType = "unknown"
    employer_type_reason: str = ""
    required_years: float | None = None


@dataclass
class Extraction:
    jobs: list[JobCard]
    dropped: list[tuple[JobCard, str]] = field(default_factory=list)
    provider: str = ""


def build_prompt(snap: Snapshot) -> str:
    links = "\n".join(f"[{i}] {a.text[:80]} -> {a.href}" for i, a in enumerate(snap.anchors[:MAX_LINKS]))
    tpl = (ROOT / "prompts" / "extract_jobs.md").read_text(encoding="utf-8")
    return tpl.replace("<<TEXT>>", snap.text[:MAX_TEXT_CHARS]).replace("<<LINKS>>", links)


def validate_urls(cards: list[JobCard], hrefs: set[str]) -> Extraction:
    """Keep only cards whose jobright_url is exactly one of the page's hrefs; dedupe by URL."""
    kept, dropped, seen = [], [], set()
    for c in cards:
        if c.jobright_url not in hrefs:
            dropped.append((c, "jobright_url not found among the page's links"))
            log.warning("dropping %s / %s: URL not on page: %s", c.company, c.title, c.jobright_url)
        elif c.jobright_url in seen:
            continue
        else:
            seen.add(c.jobright_url)
            kept.append(c)
    return Extraction(jobs=kept, dropped=dropped)


def extract_jobs(snap: Snapshot) -> Extraction:
    res = complete("extract", build_prompt(snap), list[JobCard])
    out = validate_urls(res.value, snap.hrefs())
    out.provider = res.provider
    return out
