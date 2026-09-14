"""One-shot local install --client matrix."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from context_lab.hooks import CONFIG_PATHS, HARNESS_CONFIGS, global_mcp_path, install, install_global
from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes


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
        combined = (rules + "\n" + stdout).lower()
        self.assertTrue("no ambient" in combined or "none" in combined)
        self.assertIn("recall-for", rules)
        self.assertLessEqual(len(rules), 700)

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

    def test_install_empty_ticket_binds_project_baseline(self):
        code, stdout, _ = self._install("cursor", project="app", ticket="", git=False)
        self.assertEqual(code, 0)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope.project, "app")
        self.assertEqual(shown.scope.ticket, "")
        self.assertIn("project baseline", stdout.lower())
        self.assertIn("set-scope", stdout.lower())

    def test_install_omitted_ticket_binds_project_baseline(self):
        code, _, _ = self._install("cursor", project="app", git=False)
        self.assertEqual(code, 0)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope.project, "app")
        self.assertEqual(shown.scope.ticket, "")

    def test_install_ticket_without_project_fails(self):
        self.temp, self.repo = _repo()
        with self.assertRaises(AgentError) as ctx:
            install("cursor", ticket="T-1", git=False, cwd=str(self.repo))
        self.assertEqual(ctx.exception.code, "validation")
        self.assertFalse((self.repo / ".cursor" / "mcp.json").exists())

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


class InstallGlobalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self._old_home = os.environ.get("HOME")
        self._old_claude = os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        if self._old_claude is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_claude
        self.temp.cleanup()

    def _run(self, client, **kwargs):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = install_global(client, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_claude_merges_user_mcp_without_hooks(self):
        seed = self.home / ".claude.json"
        seed.write_text(
            json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}) + "\n",
            encoding="utf-8",
        )
        code, stdout, _ = self._run("claude")
        self.assertEqual(code, 0)
        data = json.loads(seed.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("other", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["context-lab"]["args"], ["mcp"])
        self.assertFalse((self.home / ".claude" / "settings.json").exists())
        self.assertIn("still off", stdout.lower())
        self.assertIn("per-repo", stdout.lower())

    def test_codex_and_cursor_global_mcp_no_ambient_hooks(self):
        code, stdout, _ = self._run("codex")
        self.assertEqual(code, 0)
        toml = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.context-lab]", toml)
        self.assertFalse((self.home / ".codex" / "hooks.json").exists())

        code, stdout, _ = self._run("cursor")
        self.assertEqual(code, 0)
        mcp = json.loads((self.home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["context-lab"]["args"], ["mcp"])
        rules = (self.home / ".cursor" / "rules" / "context-lab-memory.mdc").read_text(encoding="utf-8")
        self.assertIn("memory_context", rules)
        self.assertIn("per-repo", rules.lower() + stdout.lower())
        self.assertFalse((self.home / ".cursor" / "hooks.json").exists())
        self.assertFalse((self.home / ".git").exists())

    def test_global_refuses_without_force(self):
        self.assertEqual(self._run("claude")[0], 0)
        with self.assertRaises(AgentError) as ctx:
            install_global("claude")
        self.assertIn(ctx.exception.code, {"already_exists", "already_exists"})

    def test_global_path_helpers(self):
        self.assertEqual(global_mcp_path("claude"), self.home / ".claude.json")
        self.assertEqual(global_mcp_path("codex"), self.home / ".codex" / "config.toml")
        self.assertEqual(global_mcp_path("cursor"), self.home / ".cursor" / "mcp.json")


if __name__ == "__main__":
    unittest.main()
