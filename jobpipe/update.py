"""Updates from the git remote: `jobpipe update`, and a once-a-day "update available" notice."""

from __future__ import annotations

import subprocess
import time

from jobpipe.config import ROOT, profile_dir

STAMP = ".last-update-check"


def _git(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def incoming() -> list[str] | None:
    """One-line commit summaries the remote has that this clone lacks; None if it can't be checked
    (not a git clone, no upstream, offline)."""
    if _git("rev-parse", "--git-dir")[0] or _git("fetch", "--quiet")[0]:
        return None
    code, out = _git("log", "--oneline", "HEAD..@{u}")
    return None if code else [line for line in out.splitlines() if line]


def dirty() -> bool:
    return bool(_git("status", "--porcelain", "--untracked-files=no")[1])


def pull() -> tuple[bool, str]:
    """Fast-forward only: local code changes are never overwritten or merged silently."""
    code, out = _git("pull", "--ff-only", timeout=180)
    return code == 0, out


def notice() -> str:
    """A one-line message when a newer version exists; checks at most once a day, never raises."""
    try:
        stamp = profile_dir() / STAMP
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
            return ""
        if not profile_dir().exists():
            return ""
        stamp.write_text("", encoding="utf-8")
        new = incoming()
    except Exception:
        return ""
    return f"update available ({len(new)} new change(s)): run `uv run jobpipe update`" if new else ""
