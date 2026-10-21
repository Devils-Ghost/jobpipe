import subprocess
from types import SimpleNamespace

import pytest
import yaml

from jobpipe import facts, projects
from jobpipe.tailor import NewProject

NP = NewProject(
    slug="payments-rpc-ledger",
    name="Payments RPC Ledger",
    why="Covers Kotlin and RPC frameworks",
    stack=["Kotlin", "gRPC"],
    resume_bullets=["Sustained **2,000** RPS by building a **gRPC** ledger in **Kotlin**.", "Cut p99 latency by **30%** using **Kotlin** coroutines."],
    metrics=[{"claim": "2,000 RPS", "how_to_measure": "k6 load test"}, {"claim": "30% p99 cut", "how_to_measure": "ghz benchmark"}],
    milestones=["Proto + server", "Load test", "Write-up"],
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Project folders under tmp/projects, pool file under tmp, README generation faked."""
    root = tmp_path / "projects"
    pool_file = tmp_path / "projects_pool.yaml"
    pool_file.write_text(yaml.safe_dump({"projects": [{"slug": "eks-serverless-migration", "status": "proposed", "resume_bullets": []}]}))
    monkeypatch.setattr(projects, "local_root", lambda: root)
    monkeypatch.setattr(projects, "path", lambda key: pool_file)
    monkeypatch.setattr(facts, "path", lambda key: pool_file)
    calls = []

    def fake_readme(np):
        calls.append(np.slug)
        return f"# {np.name}\n\n" + "\n".join(np.resume_bullets) + "\n", "fake"

    monkeypatch.setattr(projects, "generate_readme", fake_readme)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    return SimpleNamespace(root=root, pool_file=pool_file, job_dir=job_dir, readme_calls=calls)


def test_strip_fence():
    assert projects._strip_fence("```markdown\n# Title\n\nBody\n```") == "# Title\n\nBody\n"
    assert projects._strip_fence("# Title\n") == "# Title\n"


def test_readme_keeps_contract_verbatim(monkeypatch):
    monkeypatch.setattr(projects, "complete", lambda role, prompt, schema: SimpleNamespace(value="# Payments RPC Ledger\nParaphrased.", provider="fake"))
    text, provider = projects.generate_readme(NP)
    assert provider == "fake"
    assert all(b in text for b in NP.resume_bullets)  # appended when the model paraphrased


def test_creates_local_project_folder(sandbox):
    outs = projects.handle_new_projects([NP], sandbox.job_dir, "2026-10-15/Acme_x_1")
    folder = sandbox.root / NP.slug
    assert outs[0].created and not outs[0].error and outs[0].folder == str(folder)
    assert (folder / "README.md").read_text(encoding="utf-8").startswith("# Payments RPC Ledger")
    assert (folder / ".gitignore").exists()
    assert (sandbox.job_dir / f"new_project_{NP.slug}_README.md").exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=folder, capture_output=True, text=True)
    assert "Add README" in log.stdout  # local repo with one commit, nothing pushed
    remotes = subprocess.run(["git", "remote"], cwd=folder, capture_output=True, text=True)
    assert remotes.stdout.strip() == ""


def test_existing_folder_is_never_touched(sandbox):
    folder = sandbox.root / NP.slug
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# my edited README\n", encoding="utf-8")
    (folder / "src.py").write_text("print('work in progress')\n", encoding="utf-8")
    outs = projects.handle_new_projects([NP], sandbox.job_dir, "2026-10-16/Beta_y_2")
    assert not outs[0].created and not outs[0].error
    assert (folder / "README.md").read_text(encoding="utf-8") == "# my edited README\n"
    assert sandbox.readme_calls == []  # no LLM call for a reused project
    assert (sandbox.job_dir / f"new_project_{NP.slug}_README.md").read_text(encoding="utf-8") == "# my edited README\n"


def test_pool_records_path_and_jobs(sandbox):
    projects.handle_new_projects([NP], sandbox.job_dir, "2026-10-15/Acme_x_1")
    projects.handle_new_projects([NP.model_copy(update={"resume_bullets": ["changed"]})], sandbox.job_dir, "2026-10-16/Beta_y_2")
    data = yaml.safe_load(sandbox.pool_file.read_text())
    entry = next(p for p in data["projects"] if p["slug"] == NP.slug)
    assert entry["status"] == "proposed" and entry["path"].endswith(NP.slug) and entry["repo"] is None
    assert entry["resume_bullets"] == NP.resume_bullets  # first use fixes the contract
    assert entry["used_by"] == ["2026-10-15/Acme_x_1", "2026-10-16/Beta_y_2"]
    assert sandbox.pool_file.read_text().startswith("# projects_pool.yaml")


def test_unsafe_slug_rejected(sandbox):
    bad = NP.model_copy(update={"slug": "../escape"})
    outs = projects.handle_new_projects([bad], sandbox.job_dir, "x")
    assert "unsafe project slug" in outs[0].error
    assert not (sandbox.root.parent / "escape").exists()


def test_local_root_resolves_relative_to_repo():
    from jobpipe.config import ROOT, settings

    assert projects.local_root() == (ROOT / settings()["projects"]["local_root"]).resolve()
