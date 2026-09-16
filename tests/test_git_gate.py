"""Commit recall lease and git gate."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import initiate
from context_lab.hooks import (
    gate_git,
    install_git,
    lease_path,
    recall_for,
    set_scope,
)
from context_lab.store import Store
from tests.git_support import git_env, run_git


def _git(cwd, *args, home, check=True):
    return run_git(cwd, *args, home=home, check=check)


def _recall(cwd, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return recall_for(cwd=cwd, **kwargs)


class GitGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", home=self.root)
        _git(self.repo, "config", "user.email", "lab@example.com", home=self.root)
        _git(self.repo, "config", "user.name", "Lab", home=self.root)
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt", home=self.root)
        _git(self.repo, "commit", "-m", "init", home=self.root)
        self.db = str(self.root / "memory.sqlite3")
        self.store = Store(self.db)
        with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
            initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        set_scope("app", "T-1", db=self.db, cwd=str(self.repo))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_missing_lease_blocks(self):
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_recall_meter_counts_printed_context(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(recall_for(cwd=str(self.repo)), 0)
        row = self.store.db.execute("SELECT * FROM usage_events").fetchone()
        self.assertEqual((row["project"], row["ticket"], row["channel"], row["operation"]),
                         ("app", "T-1", "hook", "recall-for"))
        self.assertEqual(row["request_estimated_tokens"], 0)
        self.assertEqual(row["response_estimated_tokens"], (len(out.getvalue().rstrip("\n").encode()) + 3) // 4)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0], 1)

    def test_valid_lease_allows_and_mutations_block(self):
        self.assertEqual(_recall(str(self.repo)), 0)
        self.assertEqual(gate_git(cwd=str(self.repo)), 0)

        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        _git(self.repo, "add", "b.txt", home=self.root)
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

        self.assertEqual(_recall(str(self.repo)), 0)
        self.assertEqual(gate_git(cwd=str(self.repo)), 0)
        _git(self.repo, "commit", "-m", "add b", home=self.root)
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_expired_lease_blocks(self):
        self.assertEqual(_recall(str(self.repo)), 0)
        path = lease_path(cwd=str(self.repo))
        lease = json.loads(path.read_text(encoding="utf-8"))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lease["expires_at"] = past
        path.write_text(json.dumps(lease), encoding="utf-8")
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_absent_run_id_blocks(self):
        self.assertEqual(_recall(str(self.repo)), 0)
        path = lease_path(cwd=str(self.repo))
        lease = json.loads(path.read_text(encoding="utf-8"))
        lease["run_id"] = "run-does-not-exist"
        path.write_text(json.dumps(lease), encoding="utf-8")
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_failed_recall_writes_no_lease(self):
        path = lease_path(cwd=str(self.repo))
        if path.exists():
            path.unlink()
        with self.assertRaises(Exception):
            _recall(str(self.repo), budget=1)
        self.assertFalse(path.exists())

    def test_lease_survives_subdirectory_cwd(self):
        sub = self.repo / "sub"
        sub.mkdir()
        self.assertEqual(_recall(str(sub)), 0)
        self.assertEqual(gate_git(cwd=str(self.repo)), 0)
        self.assertEqual(gate_git(cwd=str(sub)), 0)

    def test_installed_hook_gates_real_git_commit(self):
        self.assertEqual(install_git(cwd=str(self.repo)), 0)
        # No PYTHONPATH: the script must locate the checkout through its baked CONTEXT_LAB_HOME.
        env = git_env(self.root, {k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        _git(self.repo, "add", "b.txt", home=self.root)
        blocked = subprocess.run(["git", "commit", "-m", "no lease"], cwd=self.repo,
                                 capture_output=True, text=True, env=env)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("missing lease", blocked.stderr)
        self.assertEqual(_recall(str(self.repo)), 0)
        allowed = subprocess.run(["git", "commit", "-m", "leased"], cwd=self.repo,
                                 capture_output=True, text=True, env=env)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_installed_hook_names_the_fix_when_not_installed(self):
        with patch("context_lab.hooks.CONTEXT_LAB_HOME", str(self.root / "nowhere")):
            self.assertEqual(install_git(cwd=str(self.repo)), 0)
        # A bin dir with only git and a python3 that skips site-packages (-S), so neither the
        # console script nor an editable install of context_lab is reachable.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        os.symlink(shutil.which("git"), bin_dir / "git")
        wrapper = bin_dir / "python3"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        env = git_env(self.root, {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PATH")})
        env["PATH"] = str(bin_dir)
        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        _git(self.repo, "add", "b.txt", home=self.root)
        blocked = subprocess.run(["git", "commit", "-m", "x"], cwd=self.repo,
                                 capture_output=True, text=True, env=env)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("not installed", blocked.stderr)
        self.assertIn("pipx install git+", blocked.stderr)
        self.assertNotIn("Traceback", blocked.stderr)

    def test_install_git_refuses_when_hooks_path_set(self):
        _git(self.repo, "config", "core.hooksPath", ".githooks", home=self.root)
        self.assertEqual(install_git(cwd=str(self.repo)), 1)
        hooks = Path(_git(self.repo, "rev-parse", "--git-path", "hooks", home=self.root).stdout.strip())
        self.assertFalse((self.repo / hooks / "pre-commit").exists())

    def test_install_git_leaves_existing_pre_commit(self):
        hooks = Path(_git(self.repo, "rev-parse", "--git-path", "hooks", home=self.root).stdout.strip())
        if not hooks.is_absolute():
            hooks = self.repo / hooks
        hooks.mkdir(parents=True, exist_ok=True)
        existing = hooks / "pre-commit"
        existing.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        before = existing.read_text(encoding="utf-8")
        self.assertEqual(install_git(cwd=str(self.repo)), 1)
        self.assertEqual(existing.read_text(encoding="utf-8"), before)

    def test_worktrees_hold_separate_scopes_and_leases(self):
        _git(self.repo, "branch", "wt-b", home=self.root)
        wt = self.root / "wt"
        _git(self.repo, "worktree", "add", str(wt), "wt-b", home=self.root)
        db2 = str(self.root / "memory2.sqlite3")
        store2 = Store(db2)
        try:
            with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
                initiate(store2, "app", "T-2", knowledge={"mode": "empty", "vault": "none"})
            set_scope("app", "T-2", db=db2, cwd=str(wt))
            self.assertEqual(_recall(str(self.repo)), 0)
            self.assertEqual(_recall(str(wt)), 0)
            lease_a = json.loads(lease_path(cwd=str(self.repo)).read_text(encoding="utf-8"))
            lease_b = json.loads(lease_path(cwd=str(wt)).read_text(encoding="utf-8"))
            self.assertEqual(lease_a["ticket"], "T-1")
            self.assertEqual(lease_b["ticket"], "T-2")
            self.assertNotEqual(lease_a["worktree_git_dir"], lease_b["worktree_git_dir"])
            self.assertEqual(gate_git(cwd=str(self.repo)), 0)
            self.assertEqual(gate_git(cwd=str(wt)), 0)
        finally:
            store2.close()


if __name__ == "__main__":
    unittest.main()
