"""`jobpipe feed`: Recommended jobs -> filters -> pending URLs in a career-ops pipeline.md.

No tailoring. LLM calls are limited to reading job cards and job pages (the same reads `run` does).
Format and locking follow career-ops: modes/pipeline.md (line shape), scan.mjs appendToPipeline
(insert inside the Pending section) and pipeline-lock.mjs (a `<file>.lock` directory), so the feed
can run while career-ops scans. Existing lines are never rewritten or reordered; the file is
replaced atomically.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from jobpipe import filters
from jobpipe.scrape.browser import CardCollector, browser_context, polite_pause, snapshot_page
from jobpipe.config import path, settings
from jobpipe.scrape.extract import extract_jobs
from jobpipe.scrape.jd import JobPage, NotAJobPage, analyze, posting_url, use_original_posting
from jobpipe.pipeline import job_key
from jobpipe.run import NetworkDown, _applied_rows, _blacklist, listing_reason, network_up, page_reason

log = logging.getLogger(__name__)

PENDING_MARKERS = ("## Pending", "## Pendientes")
SKELETON = "# Pipeline — Pending URLs\n\nPaste job URLs below as `- [ ] {url}` then run `/career-ops pipeline`.\n\n## Pending\n\n## Processed\n"

# career-ops url-key.mjs TRACKING_PARAMS, plus Jobright's own jr_id. Functional params (gh_jid ...) stay.
TRACKING_PARAMS = [re.compile(p, re.I) for p in (
    r"^utm_", r"^gh_src$", r"^fbclid$", r"^gclid$", r"^mc_cid$", r"^mc_eid$", r"^igshid$",
    r"^_hsenc$", r"^_hsmi$", r"^trk$", r"^trackingid$", r"^jr_",
)]
URL_RE = re.compile(r"https?://[^\s|)\]>]+")


class CareerOpsNotConfigured(Exception):
    pass


class PipelineLockTimeout(Exception):
    pass


# ---------------------------------------------------------------- URLs


def _is_tracking(name: str) -> bool:
    return any(p.search(name) for p in TRACKING_PARAMS)


def clean_url(raw: str) -> str:
    """The URL as it is written to pipeline.md: tracking parameters removed, everything else untouched."""
    u = raw.strip().split()[0] if raw.strip() else ""
    parts = urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return u
    kept = [seg for seg in parts.query.split("&") if seg and not _is_tracking(unquote(seg.split("=", 1)[0]))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def normalize_url(raw: str) -> str:
    """Comparison key, a port of career-ops url-key.mjs normalizeUrl: https, lowercase host without
    the trailing dot, tracking params dropped, remaining query sorted, fragment and trailing slash
    dropped. '' means no usable key (never matches anything)."""
    s = (raw or "").strip()
    try:
        parts = urlsplit(s)
        host = parts.hostname or ""
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host:
        return ""
    if len(host) > 1 and host.endswith(".") and not host.endswith(".."):
        host = host[:-1]
    netloc = host + (f":{parts.port}" if parts.port else "")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k))
    p = parts.path
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return urlunsplit(("https", netloc, p, urlencode(query), ""))


def employer_url(job: JobPage) -> str:
    """The employer's posting page: where the description was read from, else the apply link
    mapped from an application form to its posting. '' when Jobright shows no employer link."""
    if job.original_url:
        return job.original_url
    if job.apply_url:
        return posting_url(job.apply_url)
    return ""


# ---------------------------------------------------------------- career-ops files


@dataclass
class CareerOps:
    root: Path
    data: Path

    @property
    def pipeline(self) -> Path:
        return self.data / "data" / "pipeline.md"

    @property
    def applications(self) -> Path:
        primary = self.data / "data" / "applications.md"
        return primary if primary.exists() else self.data / "applications.md"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    @property
    def blacklist(self) -> Path:
        return self.data / "data" / "blacklist.md"


def career_ops() -> CareerOps:
    cfg = settings().get("career_ops") or {}
    raw = str(cfg.get("path") or "").strip()
    if not raw:
        raise CareerOpsNotConfigured("career_ops.path is not set in profile/settings.yaml")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise CareerOpsNotConfigured(f"career_ops.path does not exist: {root}")
    data = str(cfg.get("data_root") or "").strip()
    if not data:  # career-ops DATA_CONTRACT: a .career-ops-data marker file may point elsewhere
        marker = root / ".career-ops-data"
        data = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    data_root = (root / data if data and not Path(data).is_absolute() else Path(data)) if data else root
    return CareerOps(root=root, data=data_root)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def known_urls(co: CareerOps) -> dict[str, str]:
    """normalized URL -> where it was found, from pipeline.md, the applications tracker, and the
    `**URL:**` line of each report."""
    found: dict[str, str] = {}

    def add(text: str, label: str) -> None:
        for m in URL_RE.finditer(text):
            key = normalize_url(m.group(0).rstrip(".,;"))
            if key:
                found.setdefault(key, label)

    add(_read(co.pipeline), "pipeline.md")
    add(_read(co.applications), "applications.md")
    if co.reports.is_dir():
        for rp in co.reports.glob("*.md"):
            try:
                head = rp.read_text(encoding="utf-8")[:2000]
            except OSError:
                continue
            m = re.search(r"\*\*URL:\*\*[ \t]*(\S+)", head)
            if m:
                add(m.group(1), f"reports/{rp.name}")
    return found


def read_career_ops_blacklist(co: CareerOps) -> list[tuple[str, str]]:
    """company-scope rows of data/blacklist.md as (company, reason); domain rows are returned
    as ("domain:<suffix>", reason)."""
    out = []
    for line in _read(co.blacklist).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        name = cells[1] if len(cells) > 1 else ""
        if not name or re.fullmatch(r"[-: ]+", name) or name.lower() == "company":
            continue
        scope = (cells[3] if len(cells) > 3 else "company").lower()
        reason = cells[4] if len(cells) > 4 else ""
        out.append((f"domain:{name.lower()}" if scope == "domain" else name, reason))
    return out


def blacklist_reason(company: str, url: str, rows: list[tuple[str, str]]) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for name, why in rows:
        if name.startswith("domain:"):
            suffix = name[7:]
            if host and (host == suffix or host.endswith("." + suffix)):
                return f"blacklisted (career-ops): {suffix}" + (f": {why}" if why else "")
        elif filters.blacklisted_reason(company, [(name, why)]):
            return f"blacklisted (career-ops): {name}" + (f": {why}" if why else "")
    return None


# ---------------------------------------------------------------- pipeline.md format and writing


def _cell(value: str) -> str:
    """career-ops sanitizeMarkdownField: flatten whitespace, escape \\ [ ], and | becomes /."""
    v = re.sub(r"\s+", " ", value or "").strip()
    v = v.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return v.replace("|", "/")


def format_line(url: str, company: str, title: str, location: str = "", note: str = "") -> str:
    """`- [ ] {url} | {company} | {title} | {location} | note: {note}`; the optional parts are left
    out when empty (a labeled note rides on any row shape)."""
    u = url.strip().replace("|", "%7C").replace("[", "\\[").replace("]", "\\]")
    line = f"- [ ] {u} | {_cell(company)} | {_cell(title)}"
    if _cell(location):
        line += f" | {_cell(location)}"
    if _cell(note):
        line += f" | note: {_cell(note)}"
    return line


def insert_pending(text: str, lines: list[str]) -> str:
    """Insert `lines` at the end of the Pending section, touching nothing else. If there is no
    Pending section, one is created before Processed (or at the end), as scan.mjs does."""
    nl = "\r\n" if "\r\n" in text else "\n"
    block = nl.join(lines)
    marker = next((m for m in PENDING_MARKERS if m in text), None)
    if marker is None:
        proc = [i for i in (text.find("## Processed"), text.find("## Procesadas")) if i != -1]
        at = min(proc) if proc else len(text)
        return text[:at] + f"{nl}## Pending{nl}{nl}{block}{nl}{nl}" + text[at:]
    start = text.index(marker) + len(marker)
    nxt = text.find("\n## ", start)
    end = len(text) if nxt == -1 else nxt
    section = text[start:end]
    body_end = start + len(section.rstrip())  # just after the last non-blank character of the section
    if not section.strip():  # empty section: the lines go right under the header
        return text[:start] + nl + block + text[start:]
    return text[:body_end] + nl + block + text[body_end:]


class PipelineLock:
    """career-ops pipeline-lock.mjs protocol: an atomic `<file>.lock` directory holding owner.json.
    A lock that stays held is reported, never removed: career-ops reclaims its own stale locks, and
    deleting someone else's lock is the one unsafe move."""

    def __init__(self, target: Path, timeout: float = 20.0):
        self.dir = Path(str(target) + ".lock")
        self.token = str(uuid.uuid4())
        self.timeout = timeout

    def __enter__(self) -> "PipelineLock":
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.dir.mkdir()
                break
            except (FileExistsError, PermissionError):
                if time.monotonic() > deadline:
                    raise PipelineLockTimeout(f"{self.dir} is held by another process; try again in a moment")
                time.sleep(0.1)
        owner = {"pid": os.getpid(), "token": self.token, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "pipeline": str(self.dir)[: -len(".lock")]}
        (self.dir / "owner.json").write_text(json.dumps(owner, indent=2), encoding="utf-8")
        return self

    def __exit__(self, *exc) -> None:
        try:
            owner = json.loads((self.dir / "owner.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if owner.get("token") != self.token:
            return  # reclaimed by someone else
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


def atomic_write(target: Path, text: str) -> None:
    """Write to a temp file in the same folder, fsync, then replace. Bytes are written as given
    (no newline translation)."""
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def append_pending(pipeline: Path, url: str, line: str) -> bool:
    """Add one pending line unless the URL is already in the file. Re-reads the file under the
    lock, so a line another process just added counts as a duplicate. Returns whether it wrote."""
    with PipelineLock(pipeline):
        text = pipeline.read_bytes().decode("utf-8") if pipeline.exists() else SKELETON
        key = normalize_url(url)
        if key and any(normalize_url(m.group(0).rstrip(".,;")) == key for m in URL_RE.finditer(text)):
            return False
        atomic_write(pipeline, insert_pending(text, [line]))
        return True


def check_writable(co: CareerOps) -> tuple[bool, str]:
    """For `doctor`: pipeline.md can be appended to (or created), without changing it."""
    p = co.pipeline
    try:
        if p.exists():
            with open(p, "ab"):
                pass
            return True, str(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        probe = p.parent / f".jobpipe-probe-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"")
        probe.unlink()
        return True, f"{p} (will be created)"
    except OSError as e:
        return False, f"{p}: {e}"


# ---------------------------------------------------------------- seen ledger


def _ledger() -> Path:
    return path("applications") / ".feed.jsonl"


def seen_keys() -> dict[str, dict]:
    """Jobright job keys already decided by an earlier feed (fed, or skipped by a rule), so a
    daily list that repeats yesterday's jobs costs no LLM call."""
    out: dict[str, dict] = {}
    try:
        for ln in _ledger().read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                out[r["key"]] = r
    except (FileNotFoundError, ValueError, KeyError):
        pass
    return out


def remember(key: str, outcome: str, reason: str, day: dt.date) -> None:
    p = _ledger()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "outcome": outcome, "reason": reason, "date": day.isoformat()}) + "\n")


# ---------------------------------------------------------------- the run


@dataclass
class FeedSummary:
    loaded: int = 0
    fed: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    duplicates: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def reason_kind(reason: str) -> str:
    """'job page: sponsorship (jd): ...' -> 'sponsorship'; used to count skips by kind."""
    r = reason.removeprefix("job page: ")
    return re.split(r"[:(]", r, maxsplit=1)[0].strip() or r


def write_summary(s: FeedSummary, day: dt.date) -> Path:
    out = path("applications") / day.isoformat() / "feed.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"## {dt.datetime.now():%Y-%m-%d %H:%M}",
             f"fed {len(s.fed)}, skipped {len(s.skipped)}, duplicates {len(s.duplicates)}; read {s.loaded} cards", ""]
    for title, rows in (("Fed", s.fed), ("Skipped", s.skipped), ("Duplicates", s.duplicates)):
        if rows:
            lines.append(f"### {title}")
            lines += [f"- {r['company']} / {r['role']}: {r.get('reason') or r['url']}" for r in rows]
            lines.append("")
    with open(out, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def feed_day(limit: int, dry_run: bool = False, day: dt.date | None = None,
             echo: Callable[[str], None] = print, exclude: list[str] | None = None) -> FeedSummary:
    """Add up to `limit` jobs that pass every filter to career-ops' pipeline.md as pending URLs."""
    day = day or dt.date.today()
    co = career_ops()
    s = FeedSummary()
    cfg = settings()
    max_cards = cfg["jobright"].get("max_cards", 200)
    batch_size = max(10, min(25, limit * 2))
    note = str((cfg.get("career_ops") or {}).get("source_note", "jobright") or "")

    applied = _applied_rows(s)
    sheet_blacklist = _blacklist(s)
    co_blacklist = read_career_ops_blacklist(co)
    known = known_urls(co)
    seen = seen_keys()
    for n in s.notes:
        echo(f"NOTE {n}")

    def skip(card_company: str, role: str, url: str, reason: str, key: str = "", remember_it: bool = True) -> None:
        s.skipped.append({"company": card_company, "role": role, "url": url, "reason": reason})
        if key and remember_it and not dry_run:
            remember(key, "skipped", reason, day)
        echo(f"  skip  {card_company} / {role}: {reason}")

    with browser_context(persistent=True) as ctx:
        list_page, job_page = ctx.new_page(), ctx.new_page()
        echo("loading Jobright Recommended jobs...")
        collector = CardCollector(list_page)
        n_seen = 0
        while len(s.fed) < limit and s.loaded < max_cards:
            snap = collector.next_batch(min(batch_size, max_cards - s.loaded))
            if snap is None:
                echo("end of the Recommended list")
                break
            echo(f"reading job cards {s.loaded + 1}-{collector.handed}...")
            s.loaded = collector.handed
            try:
                extraction = extract_jobs(snap)
            except Exception as e:  # noqa: BLE001
                if not network_up():
                    raise NetworkDown("network is down (cannot reach jobright.ai)") from e
                echo(f"stopping: could not read job cards: {type(e).__name__}: {str(e).strip().splitlines()[0][:200] if str(e).strip() else ''}")
                break
            for card, reason in extraction.dropped:
                skip(card.company, card.title, card.jobright_url, f"extraction: {reason}", remember_it=False)

            for card in extraction.jobs:
                if len(s.fed) >= limit:
                    break
                n_seen += 1
                key = job_key(card.jobright_url)
                echo(f"[{n_seen}] {card.company} / {card.title}  ({len(s.fed)}/{limit} fed)")
                if key in seen:
                    s.duplicates.append({"company": card.company, "role": card.title, "url": card.jobright_url,
                                         "reason": f"already handled by an earlier feed ({seen[key]['outcome']})"})
                    echo(f"  dup   already handled by an earlier feed ({seen[key]['outcome']})")
                    continue
                reason = listing_reason(card, applied, exclude, sheet_blacklist) \
                    or blacklist_reason(card.company, "", [r for r in co_blacklist if not r[0].startswith("domain:")])
                if reason:
                    skip(card.company, card.title, card.jobright_url, reason, key)
                    continue
                try:
                    polite_pause()
                    job = analyze(snapshot_page(job_page, card.jobright_url))
                    job.url = card.jobright_url
                    use_original_posting(job, job_page)
                    reason = page_reason(card, job)
                    url = clean_url(employer_url(job))
                    reason = reason or (blacklist_reason(job.meta.company or card.company, url, co_blacklist) if url else None)
                    if reason:
                        skip(card.company, card.title, card.jobright_url, f"job page: {reason}", key)
                        continue
                    employer = bool(url)
                    url = url or clean_url(card.jobright_url)
                    dup = known.get(normalize_url(url))
                    if dup:
                        s.duplicates.append({"company": card.company, "role": card.title, "url": url,
                                             "reason": f"already in {dup}"})
                        echo(f"  dup   already in {dup}")
                        if not dry_run:
                            remember(key, "duplicate", f"already in {dup}", day)
                        continue
                    line = format_line(url, job.meta.company or card.company, job.meta.title or card.title,
                                       job.meta.location, note if employer else f"{note} (employer link not found)".strip())
                    if not dry_run and not append_pending(co.pipeline, url, line):
                        s.duplicates.append({"company": card.company, "role": card.title, "url": url,
                                             "reason": "already in pipeline.md (added meanwhile)"})
                        echo("  dup   already in pipeline.md (added meanwhile)")
                        remember(key, "duplicate", "already in pipeline.md", day)
                        continue
                    known[normalize_url(url)] = "this run"
                    s.fed.append({"company": card.company, "role": card.title, "url": url, "reason": ""})
                    if not dry_run:
                        remember(key, "fed", url, day)
                    echo(f"  {'would feed' if dry_run else 'fed'}  {url}")
                except NotAJobPage as e:
                    skip(card.company, card.title, card.jobright_url, str(e), key)
                except PipelineLockTimeout:
                    raise
                except Exception as e:  # noqa: BLE001  (one job failing never stops the run)
                    from jobpipe.scrape.browser import NotLoggedIn

                    if isinstance(e, NotLoggedIn):
                        raise
                    if not network_up():
                        raise NetworkDown("network is down (cannot reach jobright.ai)") from e
                    log.debug("job failed: %s", card.jobright_url, exc_info=True)
                    skip(card.company, card.title, card.jobright_url,
                         f"error: {type(e).__name__}: {str(e).strip().splitlines()[0][:200] if str(e).strip() else ''}",
                         key, remember_it=False)
        if len(s.fed) < limit:
            echo(f"stopped with {len(s.fed)}/{limit}: read {s.loaded} cards (max_cards {max_cards})")
    if not dry_run and (s.fed or s.skipped or s.duplicates):
        echo(f"summary: {write_summary(s, day)}")
    return s
