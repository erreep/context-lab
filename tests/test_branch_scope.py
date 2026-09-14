"""Branch binding registry: bind, resolve, conflict, detached HEAD."""
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes, MemoryScope


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
    )


class BranchScopeTests(unittest.TestCase):
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
        self.scope_a = MemoryScope(project="app", ticket="T-1")

    def tearDown(self):
        self.temp.cleanup()

    def test_bind_and_show(self):
        resolved = BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        self.assertEqual(resolved.scope, self.scope_a)
        self.assertEqual(str(resolved.database), str(Path(self.db).resolve()))
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope, self.scope_a)

    def test_baseline_bind_and_resolve(self):
        baseline = MemoryScope(project="app", ticket="")
        resolved = BranchScopes.bind_current(str(self.repo), baseline, database=self.db)
        self.assertEqual(resolved.scope, baseline)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope, baseline)
        self.assertEqual(shown.scope.ticket, "")

    def test_baseline_bind_upgrades_legacy_nonempty_ticket_check(self):
        common = Path(_git(self.repo, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = (self.repo / common).resolve()
        path = common / "context-lab" / "branch-bindings.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.executescript("""
            CREATE TABLE repository_config (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
              memory_db TEXT NOT NULL
            );
            CREATE TABLE branch_bindings (
              branch_ref TEXT PRIMARY KEY,
              project TEXT NOT NULL CHECK (trim(project) <> ''),
              ticket TEXT NOT NULL CHECK (trim(ticket) <> ''),
              updated_at TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO repository_config (singleton, memory_db) VALUES (1, ?)",
            (self.db,),
        )
        conn.commit()
        conn.close()
        baseline = MemoryScope(project="app", ticket="")
        resolved = BranchScopes.bind_current(str(self.repo), baseline, database=self.db)
        self.assertEqual(resolved.scope, baseline)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope.ticket, "")

    def test_idempotent_rebind(self):
        first = BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        second = BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        self.assertEqual(first.scope, second.scope)
        self.assertEqual(first.branch, second.branch)

    def test_conflict_without_replace(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        other = MemoryScope(project="app", ticket="T-2")
        with self.assertRaises(AgentError) as ctx:
            BranchScopes.bind_current(str(self.repo), other, database=self.db)
        self.assertEqual(ctx.exception.code, "binding_conflict")

    def test_replace_binding(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        other = MemoryScope(project="app", ticket="T-2")
        resolved = BranchScopes.bind_current(str(self.repo), other, database=self.db, replace=True)
        self.assertEqual(resolved.scope, other)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope, other)

    def test_unbind(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        self.assertTrue(BranchScopes.unbind_current(str(self.repo)))
        self.assertFalse(BranchScopes.unbind_current(str(self.repo)))
        with self.assertRaises(AgentError) as ctx:
            BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(ctx.exception.code, "unbound_branch")

    def test_resolve_after_branch_switch(self):
        main_branch = _git(self.repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        _git(self.repo, "branch", "feature-b")
        _git(self.repo, "switch", "feature-b")
        with self.assertRaises(AgentError) as ctx:
            BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(ctx.exception.code, "unbound_branch")
        scope_b = MemoryScope(project="app", ticket="T-2")
        BranchScopes.bind_current(str(self.repo), scope_b, database=self.db)
        shown = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown.scope, scope_b)
        _git(self.repo, "switch", main_branch)
        shown_main = BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(shown_main.scope, self.scope_a)

    def test_detached_head_fails(self):
        oid = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", oid)
        with self.assertRaises(AgentError) as ctx:
            BranchScopes.resolve_current(str(self.repo))
        self.assertEqual(ctx.exception.code, "detached_head")

    def test_require_request_scope_match(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        resolved = BranchScopes.require_request_scope(str(self.repo), self.scope_a)
        self.assertEqual(resolved.scope, self.scope_a)

    def test_require_request_scope_mismatch(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        with self.assertRaises(AgentError) as ctx:
            BranchScopes.require_request_scope(str(self.repo), MemoryScope("app", "T-99"))
        self.assertEqual(ctx.exception.code, "scope_mismatch")

    def test_legacy_scope_json_written(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        scope_path = Path(
            _git(self.repo, "rev-parse", "--git-path", "context-lab").stdout.strip(),
        )
        if not scope_path.is_absolute():
            scope_path = self.repo / scope_path
        payload = (scope_path / "scope.json").read_text(encoding="utf-8")
        self.assertIn('"project": "app"', payload)
        self.assertIn('"ticket": "T-1"', payload)

    def test_list_bindings(self):
        BranchScopes.bind_current(str(self.repo), self.scope_a, database=self.db)
        rows = BranchScopes.list_bindings(str(self.repo))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "app")
        self.assertEqual(rows[0]["ticket"], "T-1")


if __name__ == "__main__":
    unittest.main()
