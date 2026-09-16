import os
import tempfile
import unittest
from pathlib import Path

from context_lab.scope import (
    BoundIdentity,
    BranchScopes,
    DetachedIdentity,
    MemoryScope,
    OutsideIdentity,
    UnboundIdentity,
    identity_at,
    place_token,
    switch_token,
)
from tests.git_support import run_git


def _git(cwd, *args, home):
    return run_git(cwd, *args, home=home)


class ScopeFingerprintTests(unittest.TestCase):
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
        self.database = self.root / "memory.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_bound_identity_uses_client_cwd_while_process_is_elsewhere(self):
        BranchScopes.bind_current(
            self.repo,
            MemoryScope("context-lab", "T-42"),
            database=self.database,
        )
        previous = os.getcwd()
        try:
            os.chdir("/tmp")
            identity = identity_at(self.repo)
        finally:
            os.chdir(previous)
        self.assertIsInstance(identity, BoundIdentity)
        self.assertEqual(identity.worktree, self.repo.resolve())
        self.assertEqual(identity.scope, MemoryScope("context-lab", "T-42"))
        self.assertEqual(identity.database, self.database.resolve())
        self.assertEqual(place_token(identity), "context-lab/T-42")

    def test_branch_name_never_becomes_a_ticket(self):
        _git(self.repo, "switch", "-c", "T-99", home=self.root)
        identity = identity_at(self.repo)
        self.assertIsInstance(identity, UnboundIdentity)
        self.assertEqual(identity.branch, "refs/heads/T-99")
        self.assertEqual(place_token(identity), "unbound:refs/heads/T-99")

    def test_detached_identity(self):
        _git(self.repo, "checkout", "--detach", home=self.root)
        identity = identity_at(self.repo)
        self.assertIsInstance(identity, DetachedIdentity)
        self.assertEqual(place_token(identity), "detached")

    def test_outside_identity(self):
        identity = identity_at(self.root)
        self.assertIsInstance(identity, OutsideIdentity)
        self.assertEqual(identity.observed_cwd, self.root.resolve())
        self.assertEqual(place_token(identity), "unavailable:not_a_worktree")

    def test_baseline_place_and_switch_tokens(self):
        BranchScopes.bind_current(
            self.repo,
            MemoryScope("context-lab", ""),
            database=self.database,
        )
        self.assertEqual(place_token(identity_at(self.repo)), "context-lab/(baseline)")
        self.assertIsNone(switch_token("context-lab/T-1", "context-lab/T-1"))
        self.assertEqual(
            switch_token("context-lab/T-1", "context-lab/T-2"),
            "ticket T-1→T-2",
        )
        self.assertEqual(
            switch_token("context-lab/T-1", "other/T-2"),
            "project context-lab→other",
        )
        self.assertEqual(
            switch_token("detached", "context-lab/T-2"),
            "place detached→context-lab/T-2",
        )


if __name__ == "__main__":
    unittest.main()
