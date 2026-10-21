from jobpipe import migrations


def test_migrations_run_once_in_order(tmp_path):
    calls = []

    def m1(p):
        calls.append(1)
        (p / "settings.yaml").write_text("new: true\n")
        return ["wrote settings"]

    def m2(p):
        calls.append(2)
        return []

    ms = [(2, "second", m2), (1, "first", m1)]
    notes = migrations.migrate(tmp_path, ms)
    assert calls == [1, 2] and notes[0] == "migration 1: first (wrote settings)" and notes[1] == "migration 2: second"
    assert migrations.profile_version(tmp_path) == 2
    assert migrations.migrate(tmp_path, ms) == [] and calls == [1, 2]  # nothing re-runs


def test_new_profile_gets_current_version(tmp_path):
    assert migrations.migrate(tmp_path, []) == []
    assert (tmp_path / ".jobpipe-version").read_text() == "0"
