"""MCP, HTTP, and CLI share one recall orchestration path."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from context_lab.agent_api import context
from context_lab.engine import DATA_ROOT
from context_lab.mcp import call
from context_lab.service import dispatch, recall_context
from context_lab.store import Store


class RecallOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))
        self.store.seed(DATA_ROOT / "memories.json")
        self.task = {"project": "fieldnote", "query": "Change button color"}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_mcp_and_http_dispatch_share_recall_context(self):
        with (
            mock.patch("context_lab.service.recall_context", wraps=recall_context) as service_recall,
            mock.patch("context_lab.agent_api.recall_context", wraps=recall_context) as agent_recall,
        ):
            mcp_view = call(self.store, "memory_context", {
                "cwd": self.temp.name,
                "task": self.task,
            })
            http_view = dispatch(self.store, "context", {"task": self.task, "detail": "inspect"})
            self.assertEqual(service_recall.call_count, 1)
            self.assertEqual(agent_recall.call_count, 1)
        self.assertEqual(
            {m["id"] for m in http_view["selected"]},
            {p["id"] for p in mcp_view["picks"]},
        )

    def test_saved_run_matches_compact_and_inspect_projections(self):
        compact = context(self.store, self.task, budget=1200, detail="agent")
        saved = self.store.run(compact["run_id"])
        inspect = dispatch(self.store, "context", {"task": self.task, "detail": "inspect"})
        selected = {m["id"] for m in saved["selected"]}
        self.assertIsNotNone(saved)
        self.assertEqual({p["id"] for p in compact["picks"]}, selected)
        self.assertEqual({m["id"] for m in inspect["selected"]}, selected)
        self.assertNotIn("trace", compact)
        self.assertIn("trace", inspect)

    def test_http_default_detail_is_inspect(self):
        view = dispatch(self.store, "context", {"task": self.task})
        self.assertIn("trace", view)
        self.assertIn("selected", view)
        self.assertIn("run_id", view)


if __name__ == "__main__":
    unittest.main()
