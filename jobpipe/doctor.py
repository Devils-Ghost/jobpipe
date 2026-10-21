"""Environment checks behind `jobpipe doctor`."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jobpipe.config import path, providers_config

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def check_python() -> Check:
    v = sys.version_info
    status = OK if (v.major, v.minor) == (3, 12) else WARN
    return Check("python", status, f"{v.major}.{v.minor}.{v.micro} at {sys.executable}")


def _cli_check(name: str, live: bool) -> Check:
    from jobpipe.llm.router import load_provider, provider_enabled

    cfg = providers_config()["providers"][name]
    if not provider_enabled(cfg):
        return Check(name, WARN, f"disabled since {cfg['enabled_until']}")
    from jobpipe.llm.base import ProviderError, ProviderUnavailable, extract_json

    provider = load_provider(name, cfg)
    try:
        exe = provider.executable()
    except ProviderUnavailable as e:
        return Check(name, FAIL, str(e))
    if not live:
        return Check(name, OK, f"found {exe} (live call skipped)")
    try:
        raw = provider.run('Return exactly this JSON object: {"ok": true}')
        ok = extract_json(raw) == {"ok": True}
    except (ProviderError, ValueError) as e:
        return Check(name, FAIL, f"live call failed: {' '.join(str(e).split())[:300]}")
    return Check(name, OK if ok else WARN, "live JSON call succeeded" if ok else f"unexpected answer: {raw[:120]}")


def check_gh() -> Check:
    if not shutil.which("gh"):
        return Check("gh", WARN, "gh not on PATH (optional: only needed to publish project folders yourself)")
    r = _run(["gh", "auth", "status"])
    text = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return Check("gh", FAIL, text[-300:])
    account = next((ln.strip() for ln in text.splitlines() if "Logged in" in ln), "logged in")
    scopes_ok = "'repo'" in text
    return Check("gh", OK if scopes_ok else WARN, account + ("" if scopes_ok else " (missing 'repo' scope)"))


def check_word() -> Check:
    if sys.platform == "darwin":
        app = Path("/Applications/Microsoft Word.app")
        if not app.exists():
            return Check("word", WARN, "Microsoft Word not found in /Applications; LibreOffice fallback only")
        return Check("word", OK, f"{app} + docx2pdf (first conversion asks for permission to control Word)")
    if sys.platform != "win32":
        return Check("word", WARN, "no Word on this OS; LibreOffice fallback only")
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE")
        exe = winreg.QueryValue(key, None)
    except OSError:
        return Check("word", FAIL, "WINWORD.EXE not registered")
    try:
        import docx2pdf  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError as e:
        return Check("word", FAIL, f"{exe} found but {e.name} missing")
    return Check("word", OK, f"{exe} + docx2pdf")


def check_libreoffice() -> Check:
    from jobpipe.render.pdf import _soffice

    found = _soffice()
    return Check("libreoffice", OK if found else WARN, found or "not installed (fallback converter only)")


def check_template(render: bool) -> Check:
    from jobpipe import resume

    try:
        tpl = resume.default().template
    except resume.NoResume as e:
        return Check("template", FAIL, str(e))
    if not tpl.exists():
        return Check("template", WARN, f"{tpl.name} not built yet (milestone 2)")
    if not render:
        return Check("template", OK, f"{tpl} exists (render skipped)")
    try:
        from jobpipe.render import smoke_render  # added in milestone 2

        pages = smoke_render()
    except Exception as e:  # noqa: BLE001
        return Check("template", FAIL, f"render failed: {e}")
    return Check("template", OK if pages == 1 else FAIL, f"master content renders to {pages} page(s)")


def check_sheet() -> Check:
    from jobpipe.tracker import TrackerNotConfigured, describe

    try:
        return Check("tracker", OK, describe())
    except TrackerNotConfigured as e:
        return Check("tracker", WARN, str(e))
    except Exception as e:  # noqa: BLE001
        return Check("tracker", FAIL, f"{type(e).__name__}: {str(e)[:300]}")


def check_career_ops() -> Check:
    from jobpipe import feed

    try:
        co = feed.career_ops()
    except feed.CareerOpsNotConfigured as e:
        if "not set" in str(e):
            return Check("career-ops", OK, "not used (optional; set career_ops.path to enable `jobpipe feed`)")
        return Check("career-ops", FAIL, f"{e} (only needed for `jobpipe feed`)")
    ok, detail = feed.check_writable(co)
    return Check("career-ops", OK if ok else FAIL, f"{co.root}; pipeline.md writable: {detail}" if ok else f"pipeline.md not writable: {detail}")


def check_browser() -> Check:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Check("browser", FAIL, "playwright not installed")
    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
    except Exception as e:  # noqa: BLE001
        return Check("browser", FAIL, f"playwright error: {e}")
    if not exe.exists():
        return Check("browser", FAIL, "Chromium missing: run `uv run playwright install chromium`")
    profile = path("browser_profile")
    if not profile.exists():
        return Check("browser", WARN, "no browser profile yet: run `jobpipe login`")
    return Check("browser", OK, f"Chromium ready, profile at {profile.name} (run `jobpipe login` if Jobright logs you out)")


def check_inputs() -> Check:
    from jobpipe import resume
    from jobpipe.config import candidate, profile_dir

    if not profile_dir().exists():
        return Check("profile", FAIL, f"no profile at {profile_dir()}: run the setup (see SETUP.md)")
    missing = [k for k in ("fact_bank", "projects_pool", "denylist") if not path(k).exists()]
    resumes = resume.all_resumes()
    if not resumes:
        missing.append("a master resume (profile/resumes/<id>/master_resume.docx)")
    if missing:
        return Check("profile", FAIL, f"missing: {missing}")
    unbuilt = [r.id for r in resumes if not r.template.exists()]
    if unbuilt:
        return Check("profile", WARN, f"templates not built for {unbuilt}: run `jobpipe build-template`")
    return Check("profile", OK, f"{candidate()['name']}: {len(resumes)} resume(s) ({', '.join(r.id for r in resumes)}), fact bank, project pool")


def run_all(live: bool = True) -> list[Check]:
    checks: list[Callable[[], Check]] = [
        check_python,
        check_inputs,
        *[(lambda n=name: _cli_check(n, live)) for name in _used_providers()],
        check_gh,
        check_word,
        check_libreoffice,
        lambda: check_template(live),
        check_sheet,
        check_career_ops,
        check_browser,
    ]
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001
            results.append(Check(getattr(fn, "__name__", "check"), FAIL, f"{type(e).__name__}: {e}"))
    return results


def _used_providers() -> list[str]:
    """Providers named in any role, in first-use order (unused ones are not checked)."""
    seen: list[str] = []
    for names in providers_config().get("roles", {}).values():
        for n in names:
            if n not in seen:
                seen.append(n)
    return seen
