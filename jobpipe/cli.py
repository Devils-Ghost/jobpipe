"""jobpipe command line."""

from __future__ import annotations

import logging
import sys

import typer

# Some Windows terminals use a legacy code page; never crash on a character it can't show.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Jobright scraper + resume tailoring.")

MARKS = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}


def _logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def doctor(
    offline: bool = typer.Option(False, "--offline", help="Skip live LLM calls and the template render."),
) -> None:
    """Check Gemini CLI, Codex, gh, Word/docx2pdf, sheet access, browser, template."""
    from jobpipe.doctor import FAIL, run_all

    results = run_all(live=not offline)
    width = max(len(c.name) for c in results)
    for c in results:
        typer.echo(f"{MARKS[c.status]} {c.name.ljust(width)}  {c.detail}")
    failed = [c for c in results if c.status == FAIL]
    raise typer.Exit(code=1 if failed else 0)


@app.command("build-template")
def build_template_cmd(
    resume_id: str = typer.Option(None, "--resume", help="Resume id (folder under profile/resumes). Default: all."),
) -> None:
    """Build template.docx from each master resume (run after adding or editing a master)."""
    from jobpipe import resume
    from jobpipe.render.template import build_template

    targets = [resume.get(resume_id)] if resume_id else resume.all_resumes()
    if not targets:
        typer.echo(f"no master resumes found: {resume.NoResume.__doc__ or ''}", err=True)
        raise typer.Exit(1)
    for r in targets:
        with resume.use(r):
            build_template(r.master, r.template)
        typer.echo(f"[{r.id}] wrote {r.template} and {r.meta_file.name}")


@app.callback()
def _main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand in ("run", "tailor", "doctor", "feed"):
        from jobpipe.update import notice

        if msg := notice():
            typer.echo(msg, err=True)


@app.command()
def update(check: bool = typer.Option(False, "--check", help="Only list what is new.")) -> None:
    """Install the newest version: pull, install libraries, update your profile, check."""
    import subprocess

    from jobpipe import update as upd

    new = upd.incoming()
    if new is None:
        typer.echo("cannot check for updates (not a git clone, no remote, or offline)")
        raise typer.Exit(1)
    if not new:
        typer.echo("already up to date")
        raise typer.Exit(0)
    typer.echo(f"{len(new)} new change(s):")
    for line in new:
        typer.echo(f"  {line}")
    if check:
        raise typer.Exit(0)
    if upd.dirty():
        typer.echo("you have local code changes: commit them first (git add -A; git commit), then run update again", err=True)
        raise typer.Exit(1)
    ok, out = upd.pull()
    if not ok:
        typer.echo(f"pull failed: {out}", err=True)
        raise typer.Exit(1)
    for cmd in (["uv", "sync"], ["uv", "run", "jobpipe", "upgrade"]):
        if subprocess.run(cmd, cwd=upd.ROOT).returncode:
            raise typer.Exit(1)


@app.command()
def upgrade() -> None:
    """After a git pull and uv sync: update the profile, rebuild templates, check."""
    from jobpipe import __version__, resume
    from jobpipe.config import profile_dir
    from jobpipe.migrations import migrate
    from jobpipe.profile_check import check_profile
    from jobpipe.render.template import LayoutError, build_template

    typer.echo(f"jobpipe {__version__}")
    if not profile_dir().exists():
        typer.echo("no profile yet: run the setup first (SETUP.md)")
        raise typer.Exit(1)
    for note in migrate() or ["profile is up to date"]:
        typer.echo(f"  {note}")
    for r in resume.all_resumes():
        try:
            build_template(r.master, r.template)
            typer.echo(f"  rebuilt template for resume {r.id}")
        except LayoutError as e:
            typer.echo(f"  resume {r.id}: {e}")
    problems, _ = check_profile()
    for p in problems:
        typer.echo(f"  FIX   {p}")
    typer.echo("upgrade done" if not problems else f"upgrade done; {len(problems)} thing(s) to fix above")
    raise typer.Exit(1 if problems else 0)


@app.command("refresh-lists")
def refresh_lists_cmd(
    date: str = typer.Option(None, help="Day folder to rebuild (YYYY-MM-DD). Default: today."),
) -> None:
    """Rebuild a day's _today/_skipped .xlsx and .csv (e.g. after closing them in Excel)."""
    import datetime as dt

    from jobpipe import lists
    from jobpipe.config import path

    day = path("applications") / (date or dt.date.today().isoformat())
    if not day.exists():
        typer.echo(f"error: no folder {day}", err=True)
        raise typer.Exit(1)
    ok = lists.refresh(day, warn=typer.echo)
    typer.echo(f"{'updated' if ok else 'partly updated'}: {day}")
    raise typer.Exit(0 if ok else 1)


@app.command("show-layout")
def show_layout_cmd(
    resume_id: str = typer.Option(None, "--resume", help="Resume id (folder under profile/resumes). Default: all."),
) -> None:
    """Show what the template builder detects in a master resume (sections, skills rows, projects)."""
    from jobpipe import resume
    from jobpipe.render.template import LayoutError, master_context

    for r in [resume.get(resume_id)] if resume_id else resume.all_resumes():
        typer.echo(f"== {r.id}: {r.master}")
        try:
            ctx = master_context(r.master)
        except LayoutError as e:
            typer.echo(f"  LAYOUT PROBLEM: {e}")
            continue
        typer.echo("  skills rows (tailored):")
        for k in ctx["skill_keys"]:
            items = "".join(s["t"] for s in ctx["skills"][k])
            typer.echo(f"    {k:<24} label={ctx['skill_labels'][k].strip()!r:<28} items={items.strip()[:60]!r}")
        typer.echo("  experience (fixed, copied verbatim):")
        for e in ctx["experience"]:
            typer.echo(f"    {e['header'][:70]}  [{len(e['bullets'])} bullets]")
        typer.echo("  projects (tailored):" if ctx["projects"] else "  projects: none found (new projects off for this resume)")
        for p in ctx["projects"]:
            typer.echo(f"    {p['title'][:60]!r} term={p['term']!r}  [{len(p['bullets'])} bullets]")
        typer.echo("  everything else (name, contact, education, ...) is copied verbatim")


@app.command("check-profile")
def check_profile_cmd() -> None:
    """Validate profile/: fact bank structure, statuses, and which master-resume projects map to facts."""
    from jobpipe.profile_check import check_profile

    problems, notes = check_profile()
    for n in notes:
        typer.echo(f"  ok    {n}")
    for p in problems:
        typer.echo(f"  FIX   {p}")
    typer.echo("profile looks good" if not problems else f"{len(problems)} problem(s) to fix")
    raise typer.Exit(1 if problems else 0)


@app.command("verify-template")
def verify_template_cmd(
    max_ratio: float = typer.Option(0.0001, help="Max fraction of differing pixels per page."),
    resume_id: str = typer.Option(None, "--resume", help="Resume id (folder under profile/resumes). Default: the default resume."),
) -> None:
    """Acceptance test: template + master content must look identical to the master PDF.
    Compared against the master with rsids stripped (see strip_rsids); expected: 0 px."""
    from jobpipe.config import ROOT
    from jobpipe.pdfdiff import compare
    from jobpipe.render import page_count, render_docx, to_pdf
    from jobpipe.render.template import master_context

    from jobpipe import resume

    r = resume.get(resume_id) if resume_id else resume.default()
    master = r.master
    out = ROOT / ".render-cache" / "verify" / r.id
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("diff_page*.png"):
        f.unlink()
    master_pdf, rendered_docx, rendered_pdf = out / "master.pdf", out / "rendered.docx", out / "rendered.pdf"
    reference_docx, reference_pdf = out / "master_norsid.docx", out / "master_norsid.pdf"
    # Word lays out runs with different rsid (revision) attributes as separate shaping
    # units, shifting glyphs by sub-pixel amounts. Generated documents carry no rsids,
    # so the fair reference is the master with rsids stripped (content otherwise untouched).
    _strip_rsids_file(master, reference_docx)
    to_pdf(master, master_pdf)
    to_pdf(reference_docx, reference_pdf)
    render_docx(master_context(master), rendered_docx, r.template)
    to_pdf(rendered_docx, rendered_pdf)

    diffs, na, nb = compare(reference_pdf, rendered_pdf, out)
    typer.echo(f"pages: master={page_count(master_pdf)} reference={na} rendered={nb}")
    ok = na == nb == 1
    for d in diffs:
        flag = "OK " if d.ratio <= max_ratio else "BAD"
        ok &= d.ratio <= max_ratio
        typer.echo(f"  {flag} vs master (rsids stripped), page {d.page}: {d.differing_pixels} px ({d.ratio:.5%}) bbox={d.bbox}")
    raw, *_ = compare(master_pdf, rendered_pdf)
    typer.echo(f"  info: vs original master: {raw[0].differing_pixels} px ({raw[0].ratio:.5%}), sub-pixel rsid shaping only")
    typer.echo(f"artifacts in {out}")
    raise typer.Exit(0 if ok else 1)


def _strip_rsids_file(src, dst) -> None:
    from docx import Document

    from jobpipe.render import strip_rsids

    doc = Document(str(src))
    strip_rsids(doc.element.body)
    doc.save(str(dst))


@app.command()
def login() -> None:
    """Open the jobpipe browser on jobright.ai so you can log in by hand (once)."""
    from jobpipe.scrape.browser import login as do_login

    do_login()
    typer.echo("browser closed; session saved in .browser-profile/")


@app.command()
def inspect(limit: int = typer.Option(10, help="Jobs to load before saving.")) -> None:
    """Load the Recommended page (no LLM) and save a sanitized fixture to tests/fixtures/."""
    from jobpipe.scrape.browser import NotLoggedIn
    from jobpipe.run import inspect_recommended

    try:
        data = inspect_recommended(limit)
    except NotLoggedIn as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    jobs = len({a["href"] for a in data["anchors"] if "/jobs/" in a["href"]})
    typer.echo(f"{data['url']} | {data['title']} | {len(data['text'])} chars, {len(data['anchors'])} links, {jobs} job-ish links")
    typer.echo(f"saved {data.get('saved_to')}")


@app.command()
def run(
    limit: int = typer.Option(30, help="Max jobs to process."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List, filter, and read job pages, but do not tailor or write files."),
    exclude: str = typer.Option("", help="Comma-separated companies to skip this run, e.g. \"Amazon,Meta\" (case-insensitive)."),
    resume_id: str = typer.Option(None, "--resume", help="Use this resume (folder name under profile/resumes) for every job instead of letting each job pick."),
    verbose: bool = False,
) -> None:
    """Full pipeline: Recommended jobs -> filters -> tailored resumes in applications/<today>/."""
    import time

    from jobpipe.scrape.browser import NotLoggedIn
    from jobpipe.run import NetworkDown, run_day

    _logging(verbose)
    started = time.monotonic()
    try:
        _cap_warning()
        names = [x.strip() for x in exclude.split(",") if x.strip()]
        if names:
            typer.echo(f"excluding: {', '.join(names)}")
        s = run_day(limit, dry_run=dry_run, echo=typer.echo, exclude=names, resume_id=resume_id)
    except NotLoggedIn as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    except NetworkDown as e:
        typer.echo(f"error: {e}. Run the same command again once you are back online; finished jobs are skipped.", err=True)
        raise typer.Exit(1)
    mins = (time.monotonic() - started) / 60
    typer.echo("")
    if dry_run:
        typer.echo(f"dry run: {len(s.to_tailor)} would be tailored, {len(s.skipped)} skipped ({mins:.1f} min)")
    else:
        typer.echo(f"{len(s.tailored)} tailored, {len(s.skipped)} skipped ({mins:.1f} min); see applications/<today>/_today.csv")


@app.command()
def feed(
    limit: int = typer.Option(30, help="Max jobs to add (after filtering)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Filter and read job pages, but write nothing."),
    exclude: str = typer.Option("", help="Comma-separated companies to skip this run, e.g. \"Amazon,Meta\" (case-insensitive)."),
    verbose: bool = False,
) -> None:
    """Recommended jobs -> filters -> pending URLs in career-ops' data/pipeline.md (no tailoring)."""
    import time

    from jobpipe import feed as feed_mod
    from jobpipe.scrape.browser import NotLoggedIn
    from jobpipe.run import NetworkDown

    _logging(verbose)
    started = time.monotonic()
    names = [x.strip() for x in exclude.split(",") if x.strip()]
    if names:
        typer.echo(f"excluding: {', '.join(names)}")
    try:
        s = feed_mod.feed_day(limit, dry_run=dry_run, echo=typer.echo, exclude=names)
    except feed_mod.CareerOpsNotConfigured as e:
        typer.echo(f"error: {e}. Set career_ops.path (see config/settings.yaml).", err=True)
        raise typer.Exit(1)
    except feed_mod.PipelineLockTimeout as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    except NotLoggedIn as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    except NetworkDown as e:
        typer.echo(f"error: {e}. Run the same command again once you are back online; fed jobs are skipped.", err=True)
        raise typer.Exit(1)
    mins = (time.monotonic() - started) / 60
    verb = "would be fed" if dry_run else "fed"
    typer.echo("")
    typer.echo(f"{len(s.fed)} {verb}, {len(s.skipped)} skipped, {len(s.duplicates)} duplicates ({mins:.1f} min)")
    if s.skipped:
        counts: dict[str, int] = {}
        for r in s.skipped:
            k = feed_mod.reason_kind(r["reason"])
            counts[k] = counts.get(k, 0) + 1
        typer.echo("  skipped by reason: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])))
    if s.fed and not dry_run:
        typer.echo("next: open career-ops, then /career-ops pipeline")


@app.command()
def tailor(
    url: str,
    force: bool = typer.Option(False, help="Redo the job even if a folder for it already exists."),
    provider: str = typer.Option(None, help="Use only this provider (e.g. codex_cli, antigravity_cli); output goes to its own folder."),
    resume_id: str = typer.Option(None, "--resume", help="Use this resume (folder name under profile/resumes) instead of letting the job pick."),
    verbose: bool = False,
) -> None:
    """Tailor a resume for a single job URL (Jobright or employer page)."""
    import datetime as dt
    import time

    from jobpipe import filters, pipeline
    from jobpipe.scrape import jd
    from jobpipe.llm.router import force_provider

    _logging(verbose)
    started = time.monotonic()
    force_provider(provider)
    key = pipeline.job_key(url) + (f"-{provider.split('_')[0]}" if provider else "")
    done = filters.processed_reason(key)
    if done and not force:
        typer.echo(f"skipped: {done} (use --force to redo)")
        raise typer.Exit(0)
    _cap_warning()
    typer.echo(f"fetching {url}")
    try:
        job = jd.fetch(url)
    except jd.NotAJobPage as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"  {job.meta.title} at {job.meta.company} ({job.meta.location}); page read by {job.provider}")
    typer.echo("tailoring (this takes a minute or two)...")
    res = pipeline.process_job(job, dt.date.today(), key, resume_id)
    cov = "not meaningful (few specific skills)" if res.coverage_pct is None else f"{res.coverage_pct:.0f}%"
    typer.echo(f"done in {time.monotonic() - started:.0f}s: {res.pdf}")
    typer.echo(f"  resume {res.resume}; must-have coverage {cov}; apply at {res.apply_url or 'n/a'}")
    if res.new_project:
        typer.echo(f"  new project: {res.new_project}")
    for f in res.flags:
        typer.echo(f"  FLAG {f}")


def _cap_warning() -> None:
    from jobpipe.facts import load_pool
    from jobpipe.tailor import unbuilt_cap_warning

    w = unbuilt_cap_warning(load_pool())
    if w:
        typer.secho(f"WARNING: {w}", fg="yellow", err=True)


if __name__ == "__main__":
    app()
