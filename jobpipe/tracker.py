"""Read-only access to the person's application tracker (settings.tracker).

  type: none          no "already applied" filter
  type: google_sheet  tabs and column letters in settings; JOBPIPE_SHEET_ID and
                      GOOGLE_APPLICATION_CREDENTIALS (service-account JSON) in .env.
                      Opened with the read-only scope, so a write is impossible.
  type: csv           a local CSV with header names mapped in settings

Never writes: trackers often have their own automation (e.g. Apps Script onEdit triggers).
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from jobpipe.config import ROOT, env, settings

READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


@dataclass(frozen=True)
class AppliedRow:
    tab: str
    company: str
    title: str
    status: str
    applied: dt.date | None = None


class TrackerNotConfigured(Exception):
    pass


# kept for older imports
SheetNotConfigured = TrackerNotConfigured


def tracker_type() -> str:
    return (settings().get("tracker") or {}).get("type", "none")


def parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _col(letter: str | None) -> int | None:
    """Spreadsheet column letter -> 0-based index ("A" -> 0, "AA" -> 26)."""
    if not letter:
        return None
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _cell(row: list[str], idx: int | None) -> str:
    return (row[idx] if idx is not None and idx < len(row) else "").strip()


def _sheet():
    import gspread

    sheet_id = env("JOBPIPE_SHEET_ID")
    cred = env("GOOGLE_APPLICATION_CREDENTIALS")
    if not sheet_id:
        raise TrackerNotConfigured("JOBPIPE_SHEET_ID is not set in .env")
    if not cred:
        raise TrackerNotConfigured("GOOGLE_APPLICATION_CREDENTIALS is not set in .env")
    cred_path = Path(cred) if Path(cred).is_absolute() else ROOT / cred
    if not cred_path.exists():
        raise TrackerNotConfigured(f"service account file not found: {cred_path}")
    return gspread.service_account(filename=str(cred_path), scopes=READONLY_SCOPES).open_by_key(sheet_id)


def _read_sheet(cfg: dict) -> list[AppliedRow]:
    book = _sheet()
    cols = {k: _col(v) for k, v in (cfg.get("columns") or {}).items()}
    if cols.get("company") is None or cols.get("title") is None:
        raise TrackerNotConfigured("tracker.google_sheet.columns needs at least company and title")
    rows = []
    for tab in cfg.get("tabs") or []:
        for r in book.worksheet(tab).get_all_values()[int(cfg.get("header_rows", 1)):]:
            company = _cell(r, cols["company"])
            if company:
                rows.append(AppliedRow(tab, company, _cell(r, cols["title"]), _cell(r, cols.get("status")),
                                       parse_date(_cell(r, cols.get("date")))))
    return rows


def _read_csv(cfg: dict) -> list[AppliedRow]:
    p = Path(cfg.get("path", "profile/applied.csv"))
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        raise TrackerNotConfigured(f"tracker CSV not found: {p}")
    cols = cfg.get("columns") or {}
    rows = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            company = (r.get(cols.get("company", "company")) or "").strip()
            if company:
                rows.append(AppliedRow(p.name, company, (r.get(cols.get("title", "title")) or "").strip(),
                                       (r.get(cols.get("status", "status")) or "").strip(),
                                       parse_date(r.get(cols.get("date", "date_applied")) or "")))
    return rows


def read_applied() -> list[AppliedRow]:
    t = tracker_type()
    cfg = (settings().get("tracker") or {}).get(t) or {}
    if t == "google_sheet":
        return _read_sheet(cfg)
    if t == "csv":
        return _read_csv(cfg)
    if t == "none":
        raise TrackerNotConfigured("no tracker configured (settings.tracker.type: none)")
    raise TrackerNotConfigured(f"unknown tracker type {t!r} (use none, google_sheet, or csv)")


def blacklist_config() -> dict | None:
    """settings.tracker.google_sheet.blacklist, if the sheet tracker is on and it is set."""
    if tracker_type() != "google_sheet":
        return None
    cfg = ((settings().get("tracker") or {}).get("google_sheet") or {}).get("blacklist") or {}
    return cfg if cfg.get("tab") and cfg.get("column") else None


def read_blacklist() -> list[tuple[str, str]]:
    """(company, reason) pairs from the sheet's blacklist column: companies always skipped.
    Empty when no blacklist is configured."""
    cfg = blacklist_config()
    if not cfg:
        return []
    col, why = _col(cfg["column"]), _col(cfg.get("reason_column"))
    out = []
    for r in _sheet().worksheet(cfg["tab"]).get_all_values()[int(cfg.get("header_rows", 1)):]:
        company = _cell(r, col)
        if company:
            out.append((company, _cell(r, why)))
    return out


def describe() -> str:
    """One line for `doctor`: what is configured and whether it reads."""
    t = tracker_type()
    if t == "none":
        raise TrackerNotConfigured("no tracker configured (already-applied filter off)")
    rows = read_applied()
    line = f"{t}: {len(rows)} applied rows readable (read-only)"
    if blacklist_config():
        line += f"; {len(read_blacklist())} blacklisted companies"
    return line
