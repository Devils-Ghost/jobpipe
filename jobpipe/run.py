"""`jobpipe run`: Recommended jobs -> filters -> job pages -> tailored resumes, for one day."""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from jobpipe import filters, lists, resume
from jobpipe.scrape.browser import CardCollector, NotLoggedIn, browser_context, load_recommended, polite_pause, snapshot_page
from jobpipe.config import path, settings
from jobpipe.scrape.extract import JobCard, extract_jobs
from jobpipe.scrape.jd import JobPage, NotAJobPage, analyze, use_original_posting
from jobpipe.pipeline import JobResult, job_key, process_job

log = logging.getLogger(__name__)

class NetworkDown(Exception):
    pass


def network_up(host: str = "jobright.ai") -> bool:
    import socket

    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _first_line(e: Exception, n: int = 200) -> str:
    return f"{type(e).__name__}: {str(e).strip().splitlines()[0][:n]}" if str(e).strip() else type(e).__name__




@dataclass
class RunSummary:
    loaded: int = 0
    extracted: int = 0
    tailored: list[JobResult] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    to_tailor: list[str] = field(default_factory=list)  # dry run: jobs that would be tailored
    notes: list[str] = field(default_factory=list)


def _applied_rows(summary: RunSummary) -> list[tuple[str, str, str]]:
    from jobpipe.tracker import TrackerNotConfigured as SheetNotConfigured, read_applied

    try:
        rows = read_applied()
    except SheetNotConfigured as e:
        summary.notes.append(f"already-applied filter OFF: {e}")
        return []
    except Exception as e:  # noqa: BLE001
        summary.notes.append(f"already-applied filter OFF: tracker read failed: {type(e).__name__}: {e}")
        return []
    return [(r.tab, r.company, r.title, r.applied) for r in rows]


def _blacklist(summary: RunSummary) -> list[tuple[str, str]]:
    from jobpipe.tracker import read_blacklist

    try:
        return read_blacklist()
    except Exception as e:  # noqa: BLE001
        summary.notes.append(f"blacklist filter OFF: tracker read failed: {type(e).__name__}: {e}")
        return []


def listing_reason(card: JobCard, applied: list[tuple], exclude: list[str] | None = None,
                   blacklist: list[tuple[str, str]] | None = None) -> str | None:
    """Filters that need only the card. The job page re-checks the content-based ones."""
    return (
        filters.excluded_reason(card.company, exclude or [])
        or filters.blacklisted_reason(card.company, blacklist or [])
        or filters.reposted_reason("", card.reposted)
        or filters.processed_reason(job_key(card.jobright_url))
        or filters.applied_reason(card.company, card.title, applied)
        or filters.sponsorship_reason("", card.sponsorship_note)
        or filters.consultancy_reason(card.company, card.employer_type)
        or filters.experience_reason(card.required_years)
    )


def page_reason(card: JobCard, job: JobPage) -> str | None:
    m = job.meta
    return (
        filters.reposted_reason(job.text, m.reposted)
        or filters.sponsorship_reason(job.text, m.sponsorship_note)
        or filters.consultancy_reason(card.company, m.employer_type, job.text)
        or filters.experience_reason(m.required_years)
        or filters.graduation_reason(m.grad_earliest, m.grad_latest, m.requires_enrollment, m.grad_note)
        or filters.relevance_reason(m.role_relevant, m.relevance_reason)
    )


def run_day(limit: int, dry_run: bool = False, day: dt.date | None = None,
            echo: Callable[[str], None] = print, exclude: list[str] | None = None,
            resume_id: str | None = None) -> RunSummary:
    """Tailor up to `limit` jobs that pass every filter, reading Recommended cards in batches
    until that many are done, the list ends, or settings.jobright.max_cards cards were read."""
    day = day or dt.date.today()
    out_dir = path("applications") / day.isoformat()
    summary = RunSummary()
    max_cards = settings()["jobright"].get("max_cards", 200)
    batch_size = max(10, min(25, limit * 2))

    def skip(company: str, role: str, url: str, reason: str) -> None:
        row = {"company": company, "role": role, "jobright_url": url, "reason": reason}
        summary.skipped.append(row)
        if not dry_run:
            lists.append(out_dir, "skipped", row, warn=echo)
        echo(f"  skip  {company} / {role}: {reason}")

    def done() -> int:
        return len(summary.to_tailor) if dry_run else len(summary.tailored)

    applied = _applied_rows(summary)
    blacklist = _blacklist(summary)
    for n in summary.notes:
        echo(f"NOTE {n}")
    timeout = settings()["pipeline"]["job_timeout_seconds"]

    with browser_context(persistent=True) as ctx:
        list_page, job_page = ctx.new_page(), ctx.new_page()
        echo("loading Jobright Recommended jobs...")
        collector = CardCollector(list_page)
        seen = 0
        while done() < limit and summary.loaded < max_cards:
            snap = collector.next_batch(min(batch_size, max_cards - summary.loaded))
            if snap is None:
                echo("end of the Recommended list")
                break
            echo(f"reading job cards {summary.loaded + 1}-{collector.handed}...")
            summary.loaded = collector.handed
            try:
                extraction = extract_jobs(snap)
            except Exception as e:  # noqa: BLE001
                if not network_up():
                    raise NetworkDown("network is down (cannot reach jobright.ai)") from e
                echo(f"stopping: could not read job cards: {_first_line(e)}")
                break
            summary.extracted += len(extraction.jobs)
            for card, reason in extraction.dropped:
                skip(card.company, card.title, card.jobright_url, f"extraction: {reason}")

            for card in extraction.jobs:
                if done() >= limit:
                    break
                seen += 1
                echo(f"[{seen}] {card.company} / {card.title}  ({done()}/{limit} done)")
                reason = listing_reason(card, applied, exclude, blacklist)
                if reason:
                    skip(card.company, card.title, card.jobright_url, reason)
                    continue
                try:
                    polite_pause()
                    job = analyze(snapshot_page(job_page, card.jobright_url))
                    job.url = card.jobright_url  # keep the Jobright URL as the job's identity
                    use_original_posting(job, job_page)  # filters and tailoring read the employer's posting
                    reason = page_reason(card, job)
                    if reason:
                        skip(card.company, card.title, card.jobright_url, f"job page: {reason}")
                        continue
                    if dry_run:
                        yrs = job.meta.required_years
                        summary.to_tailor.append(f"{card.company} / {card.title} -> apply: {job.apply_url or 'n/a'}")
                        src = (f"original posting, {len(job.text)} chars" if job.text_source == "original"
                               else f"Jobright copy ({job.source_note})")
                        echo(f"  would tailor ({'?' if yrs is None else f'{yrs:g}'} yrs; {job.meta.relevance_reason[:90]})")
                        echo(f"    description: {src}; resume: {resume.choose(job.meta.resume_id, resume_id).id}")
                        continue
                    # Tailoring and rendering run in a worker so one stuck job cannot stall the run.
                    # (no `with`: its exit would wait for a stuck worker)
                    pool = cf.ThreadPoolExecutor(max_workers=1)
                    fut = pool.submit(process_job, job, day, job_key(card.jobright_url), resume_id)
                    try:
                        res = fut.result(timeout=timeout)
                    except cf.TimeoutError:
                        skip(card.company, card.title, card.jobright_url, f"timeout after {timeout}s")
                        continue
                    finally:
                        pool.shutdown(wait=False, cancel_futures=True)
                    summary.tailored.append(res)
                    lists.append(out_dir, "today", res.csv_row(), warn=echo)
                    cov = "not meaningful" if res.coverage_pct is None else f"{res.coverage_pct:.0f}%"
                    echo(f"  done  coverage {cov}{' FLAGS: ' + '; '.join(res.flags) if res.flags else ''}")
                except NotAJobPage as e:
                    skip(card.company, card.title, card.jobright_url, str(e))
                except NotLoggedIn:
                    raise
                except Exception as e:  # noqa: BLE001  (one job failing never stops the run)
                    if not network_up():
                        # not the job's fault: stop now; a rerun picks up where this left off
                        raise NetworkDown("network is down (cannot reach jobright.ai)") from e
                    log.debug("job failed: %s", card.jobright_url, exc_info=True)
                    skip(card.company, card.title, card.jobright_url, f"error: {_first_line(e)}")
        if done() < limit:
            echo(f"stopped with {done()}/{limit}: read {summary.loaded} cards (max_cards {max_cards})")
    if not dry_run and out_dir.exists():
        lists.refresh(out_dir, warn=echo)  # catch up on any list that was open in Excel
    return summary


def inspect_recommended(limit: int, save_fixture: bool = True) -> dict[str, Any]:
    """Load the Recommended page (no LLM) and save a sanitized text+links fixture for tests."""
    import json

    from jobpipe.scrape.browser import sanitize
    from jobpipe.config import ROOT

    with browser_context(persistent=True) as ctx:
        page = ctx.new_page()
        snap = load_recommended(page, limit)
    data = {
        "url": snap.url,
        "title": snap.title,
        "text": sanitize(snap.text),
        "anchors": [{"text": sanitize(a.text), "href": a.href} for a in snap.anchors],
    }
    if save_fixture:
        f = ROOT / "tests" / "fixtures" / "recommended.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=1), encoding="utf-8")
        data["saved_to"] = str(f)
    return data
