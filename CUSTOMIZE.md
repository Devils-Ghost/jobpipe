# CUSTOMIZE.md (for the coding agent)

Prefer a setting: `config/settings.yaml` documents all; personal values go in
`profile/settings.yaml`. Code changes need a test and `uv run pytest`; template changes also
`uv run jobpipe verify-template --resume <id>`.

## Settings
| Want | Change |
|---|---|
| Other roles / seniority | `filters.target_roles`, `filters.max_required_years` |
| Graduation window / no sponsorship need | `filters.graduation_date`, `filters.require_sponsorship: false` |
| Keep staffing agencies | `filters.exclude_employer_types: []` |
| Always skip companies | tracker `blacklist`, or `config/consultancy_denylist.txt`; per run: `--exclude` |
| Reapply window | `filters.reapply_after_days` |
| Jobs per run / cards read / page delay | `run --limit`, `jobright.max_cards`, `jobright.page_delay_seconds` |
| New projects | `projects.enabled`, `projects.max_new_per_resume`, `projects.local_root`, `projects.max_unbuilt_projects` |
| New-project bullet length | `tailor.bullet_chars: [min, max]` |
| Hide a project | `tailor.exclude_projects: [id]` |
| Hyperlink a phrase | `links: {"Phrase": "https://..."}` |
| Style / banned words | copy `prompts/style.md` to `profile/style.md` |
| Smaller fonts/margins | `render.floors` |
| No Word | `render.converter: libreoffice` |
| Daily list formats | `lists.formats: [xlsx, csv]` |
| LLM order / model | `roles:` and `providers.<name>.model` in `profile/providers.yaml` |

## Add an LLM provider
One module in `jobpipe/llm/` with `Adapter(Provider)` whose `run(prompt) -> str` raises the errors
in `llm/base.py`. CLI tool: copy `claude_cli.py` (prompt on stdin, `empty_workdir()`, tools off,
`run_cli()`). OpenAI-format HTTP API: no code, use `adapter: openai_compatible` with `base_url`,
`api_key_env`, `model`. Add it to `providers:`/`roles:`, run `doctor`, add a parsing test in
`tests/test_llm.py`.

## Resume layout
- Headings missed: `profile/resumes/<id>/layout.yaml` `sections: {"<heading>": skills|experience|projects|static}`; check with `show-layout`.
- Bullets must be Word list paragraphs. Tables/text boxes/columns: rebuild as single-column
  paragraphs with tab stops, or extend `render/template.py` `parse()` to walk table cells.
- `verify-template` not `0 px`: open `.render-cache/verify/<id>/diff_page1.png`. A shifted line is
  an odd run property in the master (clear its formatting in Word); a shift after a link: see
  `segments()` and `_seg_runs()` in `render/template.py`; missing fonts shift everything.

## Tailor more than Skills and Projects
Experience is copied verbatim. To tailor it: give experience bullets a loop like project bullets in
`render/template.py` `build_template`, add `roles` to `tailor/context.py` `build_context`, add a
section to `prompts/tailor.md`, and validate in `tailor/validate.py` (`numbers()`, `xyz_problems()`).

## Other sources and trackers
- Job source: a collector like `scrape/browser.CardCollector`, reuse `scrape/extract` and
  `scrape/jd`, add `--source` to `run`; keep `pipeline.job_key()` stable. No automated logins.
- Empty job pages: add the board's public API to `scrape/jd.py` `ats_api_text` (+ test), or map
  application-form links in `posting_url`.
- Tracker: `tracker.google_sheet.columns` takes letters, `tracker.csv.columns` header names; a new
  format = a reader in `tracker.py` and a `tracker.type`. Never write to it.
