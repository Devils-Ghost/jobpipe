import compileall

from typer.testing import CliRunner

from jobpipe.cli import app
from jobpipe.config import ROOT


def test_every_module_compiles():
    assert compileall.compile_dir(str(ROOT / "jobpipe"), quiet=1, force=True)


def test_all_commands_have_help():
    runner = CliRunner()
    for cmd in ["doctor", "build-template", "verify-template", "login", "inspect", "run", "feed", "tailor", "upgrade", "update", "check-profile", "show-layout"]:
        res = runner.invoke(app, [cmd, "--help"])
        assert res.exit_code == 0, (cmd, res.output)
