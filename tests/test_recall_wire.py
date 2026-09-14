"""Flat memory_context wire and non-throwing memory_scope."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import DATA_ROOT
from context_lab.mcp import TOOLS, call
from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes, MemoryScope, scope_status
from context_lab.store import Store


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class RecallWireTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.seed(DATA_ROOT / "memories.json")

    def tearDown(self):
        self.store.close()

    def test_tools_list_advertises_flat_project_and_task(self):
        schema = next(t for t in TOOLS if t["name"] == "memory_context")
        props = schema["inputSchema"]["properties"]
        self.assertEqual(schema["inputSchema"]["required"], ["project", "task"])
        self.assertEqual(props["task"]["type"], "string")
        self.assertNotIn("properties", props["task"])

    def test_verbatim_flat_payload_recalls(self):
        view = call(self.store, "memory_context", {
            "project": "fieldnote",
            "task": "Change button color",
        })
        self.assertIn("C-brand", view["context"])
        other = call(self.store, "memory_context", {
            "project": "data-dbt",
            "task": "compile branded content export DPG_REVENUE_BRANDED_CONTENT",
        })
        self.assertIn("context", other)

    def test_missing_project_names_the_field(self):
        with self.assertRaises(AgentError) as ctx:
            call(self.store, "memory_context", {"task": "Change button color"})
        self.assertEqual(ctx.exception.field, "project")
        self.assertIn("my-project", ctx.exception.hint)

    def test_query_alias_recalls(self):
        view = call(self.store, "memory_context", {
            "project": "fieldnote",
            "query": "Change button color",
        })
        self.assertIn("C-brand", view["context"])

    def test_nested_task_still_works(self):
        view = call(self.store, "memory_context", {
            "task": {"project": "fieldnote", "query": "Change button color", "as_of": "2026-09-12"},
        })
        self.assertIn("C-brand", view["context"])

    def test_mixed_flat_and_nested_rejected(self):
        with self.assertRaises(AgentError) as ctx:
            call(self.store, "memory_context", {
                "project": "data-dbt",
                "task": {"project": "fieldnote", "query": "Change button color"},
            })
        self.assertEqual(ctx.exception.code, "validation")
        self.assertEqual(ctx.exception.field, "task")
        self.assertIn("project", ctx.exception.hint)

class ScopeStatusTests(unittest.TestCase):
    def test_non_worktree_cwd_is_unavailable(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        status = scope_status(cwd=empty.name)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["reason"], "mcp_process_cwd_not_worktree")
        self.assertIn("memory_context", status["message"])
        self.assertNotIn("scope bind", status["message"].lower())

    def test_bound_worktree_is_available(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name) / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "lab@example.com")
        _git(repo, "config", "user.name", "Lab")
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "init")
        db = str(Path(temp.name) / "memory.sqlite3")
        BranchScopes.bind_current(str(repo), MemoryScope("app", "T-1"), database=db)
        status = scope_status(cwd=str(repo))
        self.assertEqual(status["status"], "available")
        self.assertEqual(status["project"], "app")
        self.assertEqual(status["ticket"], "T-1")

    def test_memory_scope_tool_does_not_raise_off_worktree(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        previous = os.getcwd()
        os.chdir(empty.name)
        try:
            data = call(store, "memory_scope", {})
        finally:
            os.chdir(previous)
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["reason"], "mcp_process_cwd_not_worktree")


if __name__ == "__main__":
    unittest.main()
