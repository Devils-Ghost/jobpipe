"""Safety net for publishing this repository: no file git would commit may contain the owner's
details. The details come from the real profile/fact_bank.yaml, so the test only runs on the
machine that has one (it is gitignored); elsewhere it is skipped."""

import re
import subprocess
import zipfile

import pytest
import yaml

from conftest import ROOT

FACT_BANK = ROOT / "profile" / "fact_bank.yaml"
TEXT = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".csv", ".json", ".example", ".lock", ".gitignore", ".gitattributes", ""}


def private_terms() -> list[str]:
    fb = yaml.safe_load(FACT_BANK.read_text(encoding="utf-8")) or {}
    contact = fb.get("contact") or {}
    terms = {str(contact.get(k) or "").strip() for k in ("name", "email", "phone", "linkedin", "github")}
    terms |= {p for p in str(contact.get("name") or "").split() if len(p) >= 3}
    for url in (contact.get("linkedin"), contact.get("github")):
        m = re.search(r"(?:linkedin\.com/in|github\.com)/([^/?#]+)", str(url or ""))
        terms |= {m.group(1)} if m else set()
    terms |= {str(r.get("company") or "").split(",")[0].strip() for r in fb.get("experience") or []}
    return sorted(t for t in terms if len(t) >= 3)


def files_git_would_commit() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in out.stdout.splitlines() if (ROOT / f).is_file()]


def contents(rel: str) -> list[tuple[str, str]]:
    path = ROOT / rel
    if path.suffix in {".docx", ".xlsx"}:
        with zipfile.ZipFile(path) as z:
            return [(f"{rel}!{n}", z.read(n).decode("utf-8", "ignore")) for n in z.namelist() if n.endswith((".xml", ".rels"))]
    if path.suffix in TEXT:
        return [(rel, path.read_text(encoding="utf-8", errors="ignore"))]
    return []


@pytest.mark.skipif(not FACT_BANK.exists(), reason="no real profile on this machine")
def test_committable_files_hold_no_personal_data():
    terms = private_terms()
    assert terms, "profile/fact_bank.yaml has no contact details to check against"
    patterns = [(t, re.compile(rf"(?<![\w]){re.escape(t)}(?![\w])", re.I)) for t in terms]
    repo_url = re.compile(r"github\.com/[\w-]+/jobpipe", re.I)   # the public repo's own address is meant to be there
    found = []
    for rel in files_git_would_commit():
        for where, text in contents(rel):
            text = repo_url.sub("", text)
            found += [f"{where}: {term}" for term, pat in patterns if pat.search(text)]
    assert not found, "personal data in files that would be committed:\n" + "\n".join(found[:30])
