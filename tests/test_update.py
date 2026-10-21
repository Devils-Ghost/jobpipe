import subprocess

from jobpipe import update


def _run(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def test_incoming_pull_and_dirty(tmp_path, monkeypatch):
    remote, mine, other = tmp_path / "remote.git", tmp_path / "mine", tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(mine)], check=True, capture_output=True)
    (mine / "a.txt").write_text("1")
    _run(mine, "add", "-A")
    _run(mine, "commit", "-qm", "first")
    _run(mine, "push", "-q", "origin", "HEAD:main")
    _run(mine, "branch", "--set-upstream-to=origin/main")
    monkeypatch.setattr(update, "ROOT", mine)
    assert update.incoming() == []
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True)
    (other / "b.txt").write_text("2")
    _run(other, "add", "-A")
    _run(other, "commit", "-qm", "second")
    _run(other, "push", "-q", "origin", "HEAD:main")
    assert update.incoming() and "second" in update.incoming()[0]
    assert not update.dirty()
    (mine / "a.txt").write_text("changed")
    assert update.dirty()
    _run(mine, "checkout", "--", "a.txt")
    ok, _ = update.pull()
    assert ok and (mine / "b.txt").exists() and update.incoming() == []


def test_not_a_git_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    assert update.incoming() is None
