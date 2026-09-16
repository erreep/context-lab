"""MCP admission: exact branch bind and ID-derived scope."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from context_lab.mcp import call
from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes, MemoryScope
from context_lab.store import Store


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class AdmissionTests(unittest.TestCase):
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
        self.database = self.root / "bound.sqlite3"
        BranchScopes.bind_current(
            self.repo,
            MemoryScope("app", "T-1"),
            database=self.database,
        )
        self.store = Store(":memory:")
        self.bound_store = Store(str(self.database))
        src_t1 = self.bound_store.add_source({
            "project": "app",
            "ticket": "T-1",
            "title": "evidence",
            "body": "Ticket one evidence.",
        })
        src_t2 = self.bound_store.add_source({
            "project": "app",
            "ticket": "T-2",
            "title": "evidence",
            "body": "Ticket two evidence.",
        })
        self.bound_store.put_memories([{
            "id": "mem-t1",
            "project": "app",
            "ticket": "T-1",
            "kind": "lesson",
            "status": "confirmed",
            "title": "Bound lesson",
            "claim": "Ticket one lesson",
            "source_ids": [src_t1["id"]],
        }])
        self.bound_store.put_memories([{
            "id": "mem-t2",
            "project": "app",
            "ticket": "T-2",
            "kind": "lesson",
            "status": "confirmed",
            "title": "Sibling lesson",
            "claim": "Ticket two lesson",
            "source_ids": [src_t2["id"]],
        }])

    def tearDown(self):
        self.bound_store.close()
        self.store.close()
        self.temp.cleanup()

    def test_bound_explicit_other_project_is_scope_mismatch(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "project": "fieldnote",
                "task": "recall",
            })
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_promote_foreign_ticket_memory_is_scope_mismatch(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_promote", {
                "cwd": str(self.repo),
                "memory_id": "mem-t2",
            })
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_promote_bound_ticket_memory_succeeds(self):
        result = call(self.store, "memory_promote", {
            "cwd": str(self.repo),
            "memory_id": "mem-t1",
        })
        self.assertEqual(result["memory"]["project"], "app")
        self.assertEqual(result["memory"]["ticket"], "")


if __name__ == "__main__":
    unittest.main()
