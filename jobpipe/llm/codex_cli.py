"""Codex CLI (`codex exec`). Read-only sandbox, ephemeral session, empty temp dir."""

from __future__ import annotations

from pathlib import Path

from jobpipe.llm.base import Provider, ProviderError, empty_workdir, run_cli


class Adapter(Provider):
    def run(self, prompt: str) -> str:
        with empty_workdir() as tmp:
            out_file = Path(tmp) / "last_message.txt"
            args = [
                self.executable(), "exec", "-",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--ephemeral",
                "--color", "never",
                "-C", tmp,
                "-o", str(out_file),
            ]
            if self.config.get("model"):
                args += ["-m", self.config["model"]]
            result = run_cli(args, prompt, self.timeout, Path(tmp))
            if out_file.exists():
                return out_file.read_text(encoding="utf-8")
            if result.stdout.strip():
                return result.stdout
        raise ProviderError("codex produced no final message")
