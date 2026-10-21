# SETUP.md: guided setup (for the coding agent)

One step at a time, plain language, propose a default, wait for the answer. Do the technical work
yourself; ask before installing anything system-wide. Track progress in `profile/SETUP_STATUS.md`
(one checkbox per step); resume at the first unchecked one. Use `examples/sample_profile/` as the
format reference, never copy its content. Never invent facts.

## 1. Environment
Install **uv** (Windows `winget install astral-sh.uv`; macOS `brew install uv`), Node.js only for
npm-based LLM CLIs, and Microsoft Word (else LibreOffice and `render.converter: libreoffice`).
Then `uv sync`, `uv run playwright install chromium`, `uv run jobpipe doctor --offline`
(a "profile" FAIL is expected until step 3). New command "not recognized": restart the editor.

## 2. Profile
Create `profile/` with `settings.yaml` (`candidate: {name, field}`), `projects_pool.yaml`
(`projects: []`), `fact_bank.yaml`, `resumes/`, `SETUP_STATUS.md`. Copy `.env.example` to `.env`.

## 3. Master resumes
For each resume: a .docx (Google Docs: File > Download > Word; avoid PDF conversion) and an id.
Copy to `profile/resumes/<id>/master_resume.docx`; add `resume.yaml`:
`description: <roles it targets>` and `default: true` on one.
`uv run jobpipe show-layout` must find the skills rows, the project slots, and the experience.
Project slots: header = title on the left, GitHub repo link on the right (no dates), the same
number of bullets under each (three works well). If headings are not found, add `layout.yaml`:
`sections: {"Toolbox": skills, "Selected Work": projects, "Where I've Worked": experience}`.
Bullets must be Word lists; one paragraph per skills row. Tables, text boxes and columns are
unsupported (CUSTOMIZE.md). Then `build-template` and `verify-template --resume <id>` (`0 px`).

## 4. Fact bank (format: `examples/sample_profile/fact_bank.yaml`)
1. `contact`: name, email, phone, location, `github` (project repo links are built from it).
2. `skills.hands_on`: every skill they have used.
3. `projects`: `id` (`proj-x`), `name`, `status: built`, `tools`, `facts`, optional `resume_title`
   and `repo`, and `resume_bullets`: the final bullets, as many as the master has per slot, in
   `**bold**` markup. Each is one sentence of 160-235 characters: what it does, how it works,
   a real number. Without `resume_bullets` a project is never offered.
4. Experience and education: optional. Unsure or private facts: `status: confirm` / `no_resume`.
Run `uv run jobpipe check-profile` and fix what it lists.

## 5. Filters (`profile/settings.yaml` `filters:`, defaults in `config/settings.yaml`)
Ask: `target_roles` (relevant roles in their words), `max_required_years`, `graduation_date`
("YYYY-MM"), `require_sponsorship`, `reapply_after_days`. Staffing agencies are skipped by default.

## 6. Tracker (optional, read-only)
`tracker.type`: `none`, `csv` (`tracker.csv.path/columns`), or `google_sheet`: service account with
the Sheets API, JSON key in `secrets/`, sheet shared as Viewer, `.env`: `JOBPIPE_SHEET_ID`,
`GOOGLE_APPLICATION_CREDENTIALS`; settings: `tracker.google_sheet.tabs/columns`
(optional `blacklist: {tab, column, reason_column}`). Check with `doctor --offline`.

## 7. LLM providers
| They have | Provider | Setup |
|---|---|---|
| Claude subscription | `claude_cli` | Claude Code installed |
| ChatGPT subscription | `codex_cli` | `npm i -g @openai/codex`, `codex login` |
| Google AI Pro/Ultra | `antigravity_cli` | install `agy`, run it once |
| Free AI Studio key | `gemini_cli` | `npm i -g @google/gemini-cli`, `GEMINI_API_KEY` in `.env` |
| Anthropic / OpenAI-compatible key | `anthropic_api` / `openai_api` | key in `.env` |
| Local model | `ollama` | running server, `model` set |

Prefer existing subscriptions. Write `profile/providers.yaml`:
`roles: {extract: [...], tailor: [...], readme: [...]}`; then `doctor` until each shows OK.

## 8. career-ops (optional)
Ask: "Do you also use career-ops (a separate job-search tool)? jobpipe can hand it the filtered jobs." No: skip;
`run` and `tailor` work on their own. Yes: career-ops is a separate project, jobpipe never clones it. Give
them `https://github.com/career-ops-hq/career-ops`, let them clone and set it up (its own README), and ask
them to paste the folder path. Set `career_ops.path` in `profile/settings.yaml`, run `doctor --offline`
(career-ops must show OK), then follow career-ops' own setup docs from there. Daily use: `jobpipe feed`.

## 9. Tailoring options (only if they care)
`projects.max_new_per_resume` (default 3), `projects.enabled`, `projects.local_root`, `links:`
(phrases to hyperlink), `tailor.exclude_projects`, own style rules (`profile/style.md`).

## 10. Jobright
They need an account with preferences set. `uv run jobpipe login`: they log in by hand and close
the window. Then `uv run jobpipe inspect`.

## 11. Trial and wrap-up
`run --limit 3 --dry-run` (tune filters), `tailor <job url>` (open the PDF and `coverage.txt`
together), `run --limit 3`. Check off `SETUP_STATUS.md`, show "Daily use" in README.md, remind them
`profile/`, `.env`, `.browser-profile/` stay private.
