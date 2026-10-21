"""New projects: README (build contract), a local project folder, and the projects_pool.yaml entry.

The folder is created under settings.projects.local_root (default: next to this repo) with the
README, a starter .gitignore, and a local git repo holding one commit. Nothing is pushed: you
create the GitHub repo yourself when you start building (`gh repo create <slug> --private
--source . --push` from inside the folder).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from jobpipe import facts
from jobpipe.config import ROOT, candidate, path, settings
from jobpipe.llm import complete
from jobpipe.tailor import NewProject

log = logging.getLogger(__name__)

POOL_HEADER = (
    "# projects_pool.yaml\n"
    "# Built projects come from fact_bank.yaml. This file tracks proposed projects and their folders.\n"
    "# status: proposed | building | built\n"
)

GITIGNORE = """# dependencies and builds
node_modules/
target/
build/
dist/
.venv/
__pycache__/
*.class

# secrets and local config
.env
*.pem
*.key

# editors and OS
.idea/
.vscode/
.DS_Store
Thumbs.db
"""


@dataclass
class ProjectOutcome:
    slug: str
    folder: str = ""
    created: bool = False   # False when the folder already existed (reused, left untouched)
    readme_provider: str = ""
    error: str = ""


def local_root() -> Path:
    root = Path(settings()["projects"].get("local_root", ".."))
    return (root if root.is_absolute() else (ROOT / root)).resolve()


def project_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError(f"unsafe project slug {slug!r}")
    return local_root() / slug


def _strip_fence(md: str) -> str:
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", md, re.S)
    return (m.group(1) if m else md).strip() + "\n"


def generate_readme(np: NewProject) -> tuple[str, str]:
    """(README markdown, provider)."""
    tpl = (ROOT / "prompts" / "readme.md").read_text(encoding="utf-8").replace("<<CANDIDATE>>", candidate()["name"])
    project = yaml.safe_dump(np.model_dump(), sort_keys=False, allow_unicode=True, width=110)
    res = complete("readme", tpl.replace("<<PROJECT>>", project), None)
    text = _strip_fence(res.value)
    missing = [b for b in np.resume_bullets if b.strip() not in text]
    if missing:
        # The bullets are the contract; append them verbatim if the model paraphrased.
        text += "\n## Resume contract (verbatim)\n\n" + "\n".join(f"- {b}" for b in np.resume_bullets) + "\n"
    return text, res.provider


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, timeout=60)


def create_local_project(np: NewProject, readme: str) -> Path:
    """Create <local_root>/<slug>/ with README.md, .gitignore, and a local git repo (one commit).
    The caller checks that the folder does not exist yet."""
    folder = project_dir(np.slug)
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(readme, encoding="utf-8")
    (folder / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    for args in (("init", "-b", "main"), ("add", "README.md", ".gitignore"),
                 ("commit", "-m", "Add README: build contract and milestones")):
        r = _git(folder, *args)
        if r.returncode != 0:
            log.warning("git %s failed in %s: %s", args[0], folder, r.stderr.strip())
            break
    return folder


def update_pool(np: NewProject, folder: Path, job_folder: str) -> None:
    pool = facts.load_pool()
    projects = pool.setdefault("projects", [])
    entry = next((p for p in projects if p.get("slug") == np.slug), None)
    if entry is None:
        entry = {"slug": np.slug, "status": "proposed", "used_by": []}
        projects.append(entry)
    if not entry.get("resume_bullets"):
        # first use fixes the build contract; later jobs reuse these exact bullets
        entry.update({
            "name": np.name,
            "why": np.why,
            "stack": list(np.stack),
            "resume_bullets": list(np.resume_bullets),
            "metrics": [m.model_dump() for m in np.metrics],
            "milestones": list(np.milestones),
        })
    entry["path"] = str(folder)
    entry.setdefault("repo", None)
    entry.setdefault("used_by", [])
    if job_folder not in entry["used_by"]:
        entry["used_by"].append(job_folder)
    body = yaml.safe_dump(pool, sort_keys=False, allow_unicode=True, width=110)
    path("projects_pool").write_text(POOL_HEADER + body, encoding="utf-8")


def handle_new_projects(new_projects: list[NewProject], job_dir: Path, job_folder: str) -> list[ProjectOutcome]:
    """For each new project: make sure its local folder exists (creating it with a generated
    README the first time), copy the README into the job folder, and record it in the pool.
    Errors are recorded, never raised."""
    outcomes = []
    for np in new_projects:
        out = ProjectOutcome(slug=np.slug)
        try:
            folder = project_dir(np.slug)
            out.folder = str(folder)
            if folder.exists():
                readme_file = folder / "README.md"
                readme = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""
            else:
                readme, out.readme_provider = generate_readme(np)
                create_local_project(np, readme)
                out.created = True
            if readme:
                (job_dir / f"new_project_{np.slug}_README.md").write_text(readme, encoding="utf-8")
            update_pool(np, folder, job_folder)
        except Exception as e:  # noqa: BLE001  (one project failing never stops the job)
            log.warning("new project %s failed: %s", np.slug, e)
            out.error = f"{type(e).__name__}: {e}"
        outcomes.append(out)
    return outcomes


def outcomes_json(outcomes: list[ProjectOutcome]) -> list[dict[str, Any]]:
    return [o.__dict__ for o in outcomes]
