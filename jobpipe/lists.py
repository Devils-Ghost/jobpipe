"""The daily lists in applications/<date>/: tailored jobs (_today) and skipped jobs (_skipped).

Each row is first appended to a hidden ledger (.today.jsonl / .skipped.jsonl), then the
user-facing files are rebuilt from it, in the formats listed in settings.lists.formats:
  xlsx  _today.xlsx / _skipped.xlsx  Excel workbook: blue, underlined links; bold frozen header; filters
  csv   _today.csv  / _skipped.csv   same rows; links as =HYPERLINK() formulas (CSV has no formatting)
If a file is open in Excel (locked), the run warns and carries on; the next rebuild catches up.
CSVs written by older versions are imported into the ledger the first time a day is touched.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Callable

from jobpipe.config import path, settings

TODAY_FIELDS = ["company", "role", "location", "apply_url", "jobright_url", "folder", "resume",
                "must_have_coverage", "new_project", "flags"]
SKIPPED_FIELDS = ["company", "role", "jobright_url", "reason"]
KINDS = {"today": TODAY_FIELDS, "skipped": SKIPPED_FIELDS}

# Columns shown as clickable links: column -> link text.
LINK_COLUMNS = {
    "apply_url": "Original Job Posting",
    "jobright_url": "Open Jobright Listing",
    "folder": "Open application directory",
}
EXCEL_LINK_MAX = 255  # Excel's HYPERLINK() formula limit; longer URLs stay plain text in the CSV

_warned: set[str] = set()


# ---------------------------------------------------------------- cell helpers


def excel_link(target: str, label: str) -> str:
    """=HYPERLINK(...) so Excel shows a clickable link when it opens the CSV."""
    if not target or len(target) > EXCEL_LINK_MAX:
        return target
    return f'=HYPERLINK("{target.replace(chr(34), "%22")}","{label}")'


def excel_text(value: Any) -> str:
    """Scraped text that starts like a formula is shown as text, never evaluated."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def link_target(column: str, value: str) -> str:
    """Folders are stored relative to the applications folder; links need the full path."""
    return str(path("applications") / value) if column == "folder" else value


_FORMULA = re.compile(r'^=HYPERLINK\("(.*?)","[^"]*"\)$')


def _unformat(column: str, value: str) -> str:
    """Undo excel_link / excel_text (used when importing a CSV written by an older version)."""
    m = _FORMULA.match(value or "")
    if m:
        value = m.group(1).replace("%22", '"')
    elif value[:2] in ("'=", "'+", "'-", "'@"):
        value = value[1:]
    if column == "folder" and value:
        try:
            value = str(Path(value).relative_to(path("applications")))
        except ValueError:
            pass
    return value


# ---------------------------------------------------------------- ledger


def ledger(day_dir: Path, kind: str) -> Path:
    return day_dir / f".{kind}.jsonl"


def load_rows(day_dir: Path, kind: str) -> list[dict[str, str]]:
    """Rows from the ledger; the first time, imported from an existing CSV (older runs)."""
    led = ledger(day_dir, kind)
    if led.exists():
        return [json.loads(ln) for ln in led.read_text(encoding="utf-8").splitlines() if ln.strip()]
    old = day_dir / f"_{kind}.csv"
    if not old.exists():
        return []
    with open(old, newline="", encoding="utf-8-sig") as f:
        rows = [{k: _unformat(k, r.get(k) or "") for k in KINDS[kind]} for r in csv.DictReader(f)]
    with open(led, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def append(day_dir: Path, kind: str, row: dict[str, Any], warn: Callable[[str], None] = print) -> None:
    """Record one row, then rebuild the CSV and the workbook for that list."""
    day_dir.mkdir(parents=True, exist_ok=True)
    load_rows(day_dir, kind)  # import an older CSV before the first new row
    clean = {k: "" if row.get(k) is None else str(row.get(k)) for k in KINDS[kind]}
    with open(ledger(day_dir, kind), "a", encoding="utf-8") as f:
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    write_lists(day_dir, kind, warn)


def write_lists(day_dir: Path, kind: str, warn: Callable[[str], None] = print) -> bool:
    rows = load_rows(day_dir, kind)
    ok = True
    writers = {"xlsx": write_xlsx, "csv": write_csv}
    for fmt in formats():
        file, writer = day_dir / f"_{kind}.{fmt}", writers[fmt]
        try:
            writer(file, KINDS[kind], rows)
            _warned.discard(str(file))
        except PermissionError:
            ok = False
            if str(file) not in _warned:
                _warned.add(str(file))
                warn(f"WARNING: {file.name} is open in another program; it will be updated after the next job "
                     "(or run `jobpipe refresh-lists`).")
    return ok


def formats() -> list[str]:
    """settings.lists.formats, e.g. [xlsx] or [xlsx, csv]; unknown entries are ignored."""
    wanted = (settings().get("lists") or {}).get("formats") or ["xlsx"]
    return [f for f in ("xlsx", "csv") if f in wanted] or ["xlsx"]


def refresh(day_dir: Path, warn: Callable[[str], None] = print) -> bool:
    return all([write_lists(day_dir, k, warn) for k in KINDS if load_rows(day_dir, k) or (day_dir / f"_{k}.csv").exists()])


# ---------------------------------------------------------------- writers


def write_csv(file: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    tmp_rows = []
    for r in rows:
        out = {}
        for k in fields:
            v = r.get(k, "")
            out[k] = excel_link(link_target(k, v), LINK_COLUMNS[k]) if (k in LINK_COLUMNS and v) else excel_text(v)
        tmp_rows.append(out)
    # utf-8-sig: Excel needs the BOM to read non-ASCII text (e.g. en dashes) correctly
    with open(file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(tmp_rows)


def write_xlsx(file: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = file.stem.lstrip("_")
    ws.append(fields)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8EEF7")
    for i, r in enumerate(rows, start=2):
        for j, k in enumerate(fields, start=1):
            v = r.get(k, "")
            cell = ws.cell(row=i, column=j)
            if k in LINK_COLUMNS and v:
                cell.value = LINK_COLUMNS[k]
                cell.hyperlink = link_target(k, v)
                cell.style = "Hyperlink"  # Excel's built-in link style...
                cell.font = Font(color="0563C1", underline="single")  # ...and its look, set on the cell itself
            else:
                cell.value = v
                cell.data_type = "s"  # always text: scraped values are never evaluated as formulas
    for j, k in enumerate(fields, start=1):
        if k in LINK_COLUMNS:
            width = len(LINK_COLUMNS[k]) + 3
        else:
            width = min(max([len(k)] + [len(str(r.get(k, ""))) for r in rows]) + 2, 60)
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(file)
