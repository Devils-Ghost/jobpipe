"""Profile migrations, run by `jobpipe upgrade` after pulling a new version.

When an update changes how files in profile/ are laid out (a renamed setting, a moved file),
append a migration here instead of asking people to edit their files by hand:

    def _m1_rename_reapply(profile: Path) -> list[str]:
        ...rewrite profile/settings.yaml...
        return ["renamed filters.reapply_days to filters.reapply_after_days"]

    MIGRATIONS.append((1, "rename reapply setting", _m1_rename_reapply))

Each migration must be safe to run on a profile that never had the old layout. The profile's
current number is stored in profile/.jobpipe-version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from jobpipe.config import profile_dir

Migration = tuple[int, str, Callable[[Path], list[str]]]
MIGRATIONS: list[Migration] = []


def _version_file(profile: Path) -> Path:
    return profile / ".jobpipe-version"


def profile_version(profile: Path | None = None) -> int:
    f = _version_file(profile or profile_dir())
    try:
        return int(f.read_text(encoding="utf-8").strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def latest() -> int:
    return max((n for n, _, _ in MIGRATIONS), default=0)


def migrate(profile: Path | None = None, migrations: list[Migration] | None = None) -> list[str]:
    """Run pending migrations in order; returns what each did."""
    profile = profile or profile_dir()
    migrations = sorted(MIGRATIONS if migrations is None else migrations, key=lambda m: m[0])
    current = profile_version(profile)
    notes: list[str] = []
    for number, description, fn in migrations:
        if number <= current:
            continue
        done = fn(profile) or []
        notes.append(f"migration {number}: {description}" + (f" ({'; '.join(done)})" if done else ""))
        _version_file(profile).write_text(str(number), encoding="utf-8")
        current = number
    if not _version_file(profile).exists():
        _version_file(profile).write_text(str(current), encoding="utf-8")
    return notes
