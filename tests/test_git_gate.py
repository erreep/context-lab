"""Commit recall lease and git gate."""
import json
import os
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


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
    )


def _run(cwd, *args, check=False):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "context_lab", *args],
        cwd=cwd, capture_output=True, text=True, env=env, check=check,
    )


class GitGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "lab@example.com")
        _git(self.repo, "config", "user.name", "Lab")
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "init")
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

    def test_valid_lease_allows_and_mutations_block(self):
        self.assertEqual(recall_for(cwd=str(self.repo)), 0)
        self.assertEqual(gate_git(cwd=str(self.repo)), 0)

        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        _git(self.repo, "add", "b.txt")
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

        self.assertEqual(recall_for(cwd=str(self.repo)), 0)
        self.assertEqual(gate_git(cwd=str(self.repo)), 0)
        _git(self.repo, "commit", "-m", "add b")
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_expired_lease_blocks(self):
        self.assertEqual(recall_for(cwd=str(self.repo)), 0)
        path = lease_path(cwd=str(self.repo))
        lease = json.loads(path.read_text(encoding="utf-8"))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lease["expires_at"] = past
        path.write_text(json.dumps(lease), encoding="utf-8")
        self.assertEqual(gate_git(cwd=str(self.repo)), 1)

    def test_absent_run_id_blocks(self):
        self.assertEqual(recall_for(cwd=str(self.repo)), 0)
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
            recall_for(cwd=str(self.repo), budget=1)
        self.assertFalse(path.exists())

    def test_install_git_leaves_existing_pre_commit(self):
        hooks = Path(_git(self.repo, "rev-parse", "--git-path", "hooks").stdout.strip())
        if not hooks.is_absolute():
            hooks = self.repo / hooks
        hooks.mkdir(parents=True, exist_ok=True)
        existing = hooks / "pre-commit"
        existing.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        before = existing.read_text(encoding="utf-8")
        self.assertEqual(install_git(cwd=str(self.repo)), 1)
        self.assertEqual(existing.read_text(encoding="utf-8"), before)

    def test_worktrees_hold_separate_scopes_and_leases(self):
        _git(self.repo, "branch", "wt-b")
        wt = self.root / "wt"
        _git(self.repo, "worktree", "add", str(wt), "wt-b")
        db2 = str(self.root / "memory2.sqlite3")
        store2 = Store(db2)
        try:
            with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
                initiate(store2, "app", "T-2", knowledge={"mode": "empty", "vault": "none"})
            set_scope("app", "T-2", db=db2, cwd=str(wt))
            self.assertEqual(recall_for(cwd=str(self.repo)), 0)
            self.assertEqual(recall_for(cwd=str(wt)), 0)
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
