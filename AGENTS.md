# AGENTS.md

jobpipe reads Jobright's Recommended jobs, filters them, and writes a tailored one-page resume per
job from the user's Word resume, using only facts in their fact bank. They review and apply by
hand. The user may not be technical: plain language, one step at a time.

## Session start
1. No `profile/`, or unchecked items in `profile/SETUP_STATUS.md`: offer setup, follow SETUP.md.
2. Else `uv run jobpipe update --check`. If it lists changes, run `uv run jobpipe update` and summarize
   them; if it fails, explain in plain words.
3. Behavior changes: CUSTOMIZE.md.

## Layout
- `profile/` (gitignored): `settings.yaml`, `providers.yaml`, `fact_bank.yaml`, `projects_pool.yaml`,
  `style.md` (optional), `resumes/<id>/` (`master_resume.docx`, `resume.yaml`, `layout.yaml`).
- `config/` defaults, `prompts/` LLM prompts, `examples/sample_profile/` fictional reference
  profile (tests use it), `applications/` output (gitignored), `.env` secrets.
- `jobpipe/`: `scrape/` (browser, job cards, job pages) · `filters.py`, `tracker.py` ·
  `tailor/` (schema, layout, slots, prompt, validate, context, coverage) · `render/` (template,
  fill, pdf, fit) · `llm/` (router + providers) · `pipeline.py` (one job), `run.py` (daily run),
  `feed.py` (optional career-ops hand-off) · `projects.py` (new project folders) · `facts.py`,
  `resume.py`, `config.py`, `cli.py`, `doctor.py`.

## Commands (`uv run jobpipe ...`)
`doctor [--offline]` · `check-profile` · `show-layout` · `build-template` ·
`verify-template` (must print `0 px`) · `login` · `run --limit N [--dry-run] [--exclude "A,B"]
[--resume id]` · `tailor <url> [--force] [--provider p]` · `update` · `upgrade` · `pytest`.

## Update
`uv run jobpipe update` (`--check` lists what is new): pulls fast-forward only, runs `uv sync` and `jobpipe upgrade`.
Local code changes: commit them first. Then summarize the changes. `run`, `tailor`, `doctor` and `feed` print
"update available" once a day.

## Rules
- Never invent facts about the user; mark unsure ones `status: confirm`.
- Never write to their tracker, automate a login, or bypass site protections.
- Ask before installing anything system-wide.
- Personal data only in `profile/` or `.env`, never in `config/`, `prompts/`, code, or tests. This
  repo is public: `tests/test_no_personal_data.py` checks that nothing personal is committed.
- Prefer settings over code. Code changes need a test and a green `uv run pytest`.
- After editing a master resume: `build-template`, then `verify-template`.

## Design
- Experience is copied from the master; only Skills and Projects are tailored.
- A built project prints its fact-bank `resume_bullets` verbatim. The model picks which projects
  fill the slots (built, pool, or new) and writes bullets only for new ones.
- `agy` is an agent: its adapter prepends a no-tools line. Jobright's list is virtualized
  (`scrape.browser.CardCollector`). Word shapes runs with different rsids separately, so
  rendering strips them.
