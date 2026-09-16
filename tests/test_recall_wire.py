import os
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import DATA_ROOT
from context_lab.mcp import TOOLS, call
from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes, MemoryScope
from context_lab.store import Store
from tests.git_support import run_git


def _git(cwd, *args, home):
    return run_git(cwd, *args, home=home)


class RecallWireTests(unittest.TestCase):
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
        self.database = self.root / "bound.sqlite3"
        BranchScopes.bind_current(
            self.repo,
            MemoryScope("app", "T-1"),
            database=self.database,
        )
        self.store = Store(":memory:")
        self.bound_store = Store(str(self.database))
        self.bound_store.seed(DATA_ROOT / "memories.json")

    def tearDown(self):
        self.bound_store.close()
        self.store.close()
        self.temp.cleanup()

    def test_tools_require_client_cwd_by_tool_class(self):
        schemas = {item["name"]: item["inputSchema"] for item in TOOLS}
        self.assertEqual(schemas["memory_context"]["required"], ["cwd", "task"])
        self.assertNotIn("project", schemas["memory_context"]["required"])
        self.assertEqual(schemas["memory_scope"]["required"], ["cwd"])
        for name in {
            "memory_initiate",
            "memory_source",
            "memory_observe",
            "memory_propose",
            "memory_journal",
            "memory_park",
            "memory_inspect_run",
            "memory_feedback",
            "memory_promote",
        }:
            self.assertIn("cwd", schemas[name]["required"])
        for name in {"memory_catalog", "memory_allocate_ticket"}:
            self.assertNotIn("cwd", schemas[name]["required"])

    def test_bound_context_fills_scope_and_uses_bound_database(self):
        view = call(self.store, "memory_context", {
            "cwd": str(self.repo),
            "task": "recall",
        })
        self.assertEqual(view["place"], "app/T-1")
        run = self.bound_store.run(view["run_id"])
        self.assertEqual(run["task"]["project"], "app")
        self.assertEqual(run["task"]["ticket"], "T-1")
        self.assertIsNone(self.store.run(view["run_id"]))

    def test_context_uses_client_cwd_not_process_cwd(self):
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            view = call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "task": "recall",
            })
        finally:
            os.chdir(previous)
        self.assertEqual(view["place"], "app/T-1")

    def test_context_since_match_omits_identity_fields(self):
        view = call(self.store, "memory_context", {
            "cwd": str(self.repo),
            "task": "recall",
            "since": "app/T-1",
        })
        self.assertNotIn("place", view)
        self.assertNotIn("switch", view)
        self.assertNotIn("identity", view)
        self.assertNotIn("changes", view)

    def test_context_since_change_emits_literal_switch(self):
        view = call(self.store, "memory_context", {
            "cwd": str(self.repo),
            "task": "recall",
            "since": "app/T-0",
        })
        self.assertEqual(view["place"], "app/T-1")
        self.assertEqual(view["switch"], "ticket T-0→T-1")

    def test_cross_project_request_is_scope_mismatch(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "project": "fieldnote",
                "task": "Change button color",
            })
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_same_project_wrong_ticket_is_scope_mismatch(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "project": "app",
                "ticket": "T-2",
                "task": "recall",
            })
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_unbound_context_without_project_names_project(self):
        _git(self.repo, "switch", "-c", "unbound", home=self.root)
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "task": "recall",
            })
        self.assertEqual(caught.exception.code, "validation")
        self.assertEqual(caught.exception.field, "project")

    def test_nested_task_cross_project_is_scope_mismatch(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "task": {
                    "project": "fieldnote",
                    "query": "Change button color",
                    "as_of": "2026-09-12",
                },
            })
        self.assertEqual(caught.exception.code, "scope_mismatch")

    def test_mixed_flat_and_nested_rejected(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_context", {
                "cwd": str(self.repo),
                "project": "fieldnote",
                "task": {"project": "fieldnote", "query": "Change button color"},
            })
        self.assertEqual(caught.exception.code, "validation")
        self.assertEqual(caught.exception.field, "task")

    def test_memory_scope_is_compact(self):
        data = call(self.store, "memory_scope", {"cwd": str(self.repo)})
        self.assertEqual(data, {"place": "app/T-1"})

    def test_memory_scope_since_change_is_compact(self):
        data = call(self.store, "memory_scope", {
            "cwd": str(self.repo),
            "since": "app/T-0",
        })
        self.assertEqual(data, {
            "place": "app/T-1",
            "switch": "ticket T-0→T-1",
        })

    def test_memory_scope_without_cwd_names_cwd(self):
        with self.assertRaises(AgentError) as caught:
            call(self.store, "memory_scope", {})
        self.assertEqual(caught.exception.code, "validation")
        self.assertEqual(caught.exception.field, "cwd")

    def test_memory_scope_inspect_adds_identity_only_on_request(self):
        data = call(self.store, "memory_scope", {
            "cwd": str(self.repo),
            "detail": "inspect",
        })
        self.assertEqual(data["place"], "app/T-1")
        self.assertEqual(data["identity"]["status"], "bound")
        self.assertEqual(data["identity"]["project"], "app")
        self.assertEqual(data["identity"]["ticket"], "T-1")
        self.assertEqual(data["identity"]["database"], str(self.database.resolve()))


if __name__ == "__main__":
    unittest.main()
