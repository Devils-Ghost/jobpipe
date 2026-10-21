# jobpipe

Reads your Jobright "Recommended jobs", skips the ones not worth applying to, and writes a
tailored one-page resume per remaining job from your own Word resume, using only facts you
confirmed. You review and apply by hand.

## Start
1. Install a coding agent (Claude Code or Codex) and Git.
2. `git clone https://github.com/Devils-Ghost/jobpipe`, open the folder in the agent.
3. Say **"Set me up"** (`/setup` in Claude Code). It takes 30-60 minutes and can be split.

You need Windows or macOS, your resume as .docx, a Jobright account, Microsoft Word (or
LibreOffice), and one AI model (a Claude, ChatGPT or Google subscription, or an API key).

## Daily use
`uv run jobpipe run --limit 20` (about 5 minutes per job), then open
`applications/<today>/_today.xlsx`: **Open application directory** (PDF + `coverage.txt`),
**Original Job Posting** to apply. `_skipped.xlsx` says why jobs were skipped. After an
interruption, rerun the same command; finished jobs are skipped.

## Commands (`uv run jobpipe ...`)
| Command | Does |
|---|---|
| `run --limit N [--dry-run] [--exclude "A,B"] [--resume id]` | Daily pipeline; `--dry-run` only filters |
| `tailor <url> [--force] [--provider p]` | One job from any site |
| `feed --limit N` | Optional: filtered jobs as pending URLs for a career-ops clone (`career_ops.path`) |
| `update [--check]` | Install the newest version (profile untouched) |
| `login` | Log in to Jobright by hand |
| `doctor [--offline]` | Check everything |
| `check-profile` | Validate the fact bank |
| `show-layout`, `build-template`, `verify-template` | Resume layout; must print `0 px` |
| `upgrade` | After `git pull` + `uv sync` |
| `refresh-lists`, `inspect` | Rebuild day lists; save a test fixture |

## How the resume is built
Only Skills and Projects change; the rest is copied from your master. Each project slot (title
left, GitHub repo link right) gets the best of: a **built project** from your fact bank (its
`resume_bullets` verbatim), a **pool project** (`profile/projects_pool.yaml`), or a **new
project** designed around the job's missing skills. A poorly fitting built project gives up its
slot, so every slot can change. New projects get a local folder and README (the build contract)
under `projects.local_root`; create the GitHub repo yourself when you build it.

Rules enforced in code (one retry, then flagged): new-project bullets have the right count and
length, a number each, every number in the project's metrics, a "by/using" clause, a named
technology, no banned words (`prompts/style.md`); skills come only from your whitelist.

Filters: reposted, already processed or applied (tracker), sponsorship, years required,
graduation window, not relevant to `filters.target_roles`, staffing/IT consultancies, `--exclude`.

## Files you edit
`profile/fact_bank.yaml` (contact, skills, projects with `repo` and `resume_bullets`) ·
`profile/resumes/<id>/master_resume.docx` · `profile/projects_pool.yaml` · `profile/settings.yaml`
(overrides `config/settings.yaml`, which documents every option) · `profile/providers.yaml` ·
`profile/style.md` (optional) · `.env` (keys).

## Output
`applications/<date>/<Company>_<Role>_<id>/`: the resume (`.pdf`/`.docx`), `coverage.txt`
(must-haves matched/missing, projects picked), `jd.md`, `tailor.json`, `log.json`. Flags to check:
**validation** (rule still broken), **overflow** (over one page).

## Updates and privacy
Say "update jobpipe" (`/update`): `git pull`, `uv sync`, `uv run jobpipe upgrade`. Your resume,
facts, logins and outputs stay in `profile/`, `.env`, `secrets/`, `.browser-profile/`,
`applications/` (gitignored). The AI model sees your resume content and job descriptions, never
your phone or email.

## Troubleshooting
`uv` not recognized: restart the editor. Jobright login page: `login`. Network down: rerun.
Template off: `build-template`, `verify-template`. Otherwise `doctor` and show the output to your
agent.
