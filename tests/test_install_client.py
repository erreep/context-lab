"""One-shot local install --client matrix."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from context_lab.hooks import CONFIG_PATHS, HARNESS_CONFIGS, install
from context_lab.schemas import AgentError


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "lab@example.com")
    _git(repo, "config", "user.name", "Lab")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")
    return temp, repo


class InstallClientTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def _install(self, client, **kwargs):
        self.temp, self.repo = _repo()
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = install(client, cwd=str(self.repo), **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_claude_writes_mcp_and_ambient_hooks(self):
        code, stdout, _ = self._install(
            "claude", project="app", ticket="T-1", git=False,
        )
        self.assertEqual(code, 0)
        mcp = json.loads((self.repo / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["context-lab"]["args"], ["mcp"])
        settings = json.loads((self.repo / CONFIG_PATHS["claude"]).read_text(encoding="utf-8"))
        self.assertEqual(settings, HARNESS_CONFIGS["claude"])
        self.assertIn("UserPromptSubmit", settings["hooks"])
        self.assertIn("SessionStart", settings["hooks"])
        self.assertIn("ambient", stdout.lower())
        self.assertNotIn("no CompactView", stdout)

    def test_codex_writes_mcp_toml_and_ambient_hooks(self):
        code, stdout, _ = self._install(
            "codex", project="app", ticket="T-1", git=False,
        )
        self.assertEqual(code, 0)
        toml = (self.repo / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.context-lab]", toml)
        self.assertIn('args = ["mcp"]', toml)
        hooks = json.loads((self.repo / CONFIG_PATHS["codex"]).read_text(encoding="utf-8"))
        self.assertEqual(hooks, HARNESS_CONFIGS["codex"])
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("codex_hooks", stdout)

    def test_cursor_has_no_ambient_inject_but_writes_soft_contract(self):
        code, stdout, _ = self._install(
            "cursor", project="app", ticket="T-1", git=False,
        )
        self.assertEqual(code, 0)
        hooks = json.loads((self.repo / CONFIG_PATHS["cursor"]).read_text(encoding="utf-8"))
        self.assertEqual(hooks, HARNESS_CONFIGS["cursor"])
        self.assertNotIn("SessionStart", hooks["hooks"])
        self.assertNotIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("beforeShellExecution", hooks["hooks"])
        rules = (self.repo / ".cursor/rules/context-lab-memory.mdc").read_text(encoding="utf-8")
        self.assertIn("memory_context", rules)
        self.assertIn("no ambient", rules.lower() + " " + stdout.lower() or "")
        self.assertTrue(
            "no CompactView" in stdout
            or "none" in stdout.lower()
            or "no ambient" in stdout.lower()
        )
        self.assertIn("memory_context", stdout + rules)

    def test_refuses_existing_files_without_force(self):
        code, _, _ = self._install("claude", project="app", ticket="T-1", git=False)
        self.assertEqual(code, 0)
        with self.assertRaises(AgentError) as ctx:
            install("claude", project="app", ticket="T-1", git=False, cwd=str(self.repo))
        self.assertEqual(ctx.exception.code, "already_exists")
        # force rewrites
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self.assertEqual(
                install("claude", project="app", ticket="T-1", git=False, force=True, cwd=str(self.repo)),
                0,
            )

    def test_missing_scope_fails_before_writes(self):
        self.temp, self.repo = _repo()
        with self.assertRaises(AgentError) as ctx:
            install("claude", git=False, cwd=str(self.repo))
        self.assertEqual(ctx.exception.code, "missing_scope")
        self.assertFalse((self.repo / ".mcp.json").exists())
        self.assertFalse((self.repo / CONFIG_PATHS["claude"]).exists())

    def test_install_git_by_default(self):
        code, stdout, _ = self._install("cursor", project="app", ticket="T-1", git=True)
        self.assertEqual(code, 0)
        pre = self.repo / ".git" / "hooks" / "pre-commit"
        self.assertTrue(pre.is_file())
        self.assertIn("lease", stdout.lower())


if __name__ == "__main__":
    unittest.main()
