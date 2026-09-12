"""Wire budget: CompactView must fit budget; inspect keeps traces off the agent wire."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from context_lab.agent_api import context, inspect_run
from context_lab.engine import compile_context
from context_lab.mcp import call
from context_lab.schemas import format_context, wire_dumps, wire_estimated_tokens
from context_lab.store import Store


class WireBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))
        self.store.add_source({"id": "src-1", "project": "app", "ticket": "T-1",
                               "title": "note", "body": "Upload retries must preserve operation identity"})
        for i in range(8):
            self.store.put_memories([{
                "id": f"M-{i}", "project": "app", "ticket": "T-1", "kind": "lesson", "status": "confirmed",
                "title": f"Lesson about retries and stationery padding {i} " + ("x" * 40),
                "claim": ("Upload retries preserve operation identity. " * 12) + f"n={i}",
                "rationale": "Ticket evidence " * 20,
                "source_ids": ["src-1"],
                "need_tags": ["upload"],
            }])
        self.store.put_memories([{
            "id": "M-cand", "project": "app", "ticket": "T-1", "kind": "lesson", "status": "candidate",
            "title": "Unconfirmed candidate must not leak",
            "claim": "secret claim text", "source_ids": ["src-1"],
        }])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_full_packet_can_exceed_budget_while_compact_fits(self):
        task = {"project": "app", "ticket": "T-1", "query": "upload retry constraints", "needs": ["upload"]}
        packet = compile_context(self.store, task, budget=1200)
        full_keys, _ = format_context(packet, detail="inspect")
        self.assertLessEqual(packet["estimated_tokens"], 1200)
        compact, wire = format_context(packet, detail="agent")
        self.assertEqual(wire, wire_dumps(compact))
        self.assertEqual(compact["wire_estimated_tokens"], wire_estimated_tokens(wire))
        self.assertLessEqual(compact["wire_estimated_tokens"], 1200)

    def test_agent_context_omits_excluded_titles(self):
        view = context(self.store, {
            "project": "app", "ticket": "T-1", "query": "upload retry", "needs": ["upload"],
        }, budget=1200)
        blob = json.dumps(view)
        self.assertNotIn("Unconfirmed candidate must not leak", blob)
        self.assertNotIn("trace", view)
        self.assertNotIn("selected", view)
        self.assertTrue(view["picks"])
        self.assertLessEqual(view["wire_estimated_tokens"], 1200)

    def test_inspect_run_returns_trace(self):
        view = context(self.store, {
            "project": "app", "ticket": "T-1", "query": "upload retry", "needs": ["upload"],
        }, budget=1200)
        traced = inspect_run(self.store, view["run_id"])
        self.assertIn("trace", traced)
        self.assertIn("Unconfirmed candidate must not leak", json.dumps(traced))

    def test_mcp_text_matches_reported_wire_tokens_with_unicode(self):
        self.store.put_memories([{
            "id": "M-uni", "project": "app", "ticket": "T-1", "kind": "lesson", "status": "confirmed",
            "title": "Unicode café résumé 日本語",
            "claim": "Preserve opération identity — アップロード " * 40,
            "source_ids": ["src-1"], "need_tags": ["upload"],
        }])
        view = call(self.store, "memory_context", {
            "task": {"project": "app", "ticket": "T-1", "query": "upload opération 日本語", "needs": ["upload"]},
            "budget": 1200,
        })
        text = wire_dumps(view)
        measured = math.ceil(len(text.encode("utf-8")) / 4)
        self.assertEqual(view["wire_estimated_tokens"], measured)
        self.assertLessEqual(measured, 1200)
        legacy = json.dumps(view)
        self.assertNotEqual(text, legacy)

    def test_context_plans_once_and_saves_one_run(self):
        for i in range(4, 12):
            self.store.put_memories([{
                "id": f"M-extra-{i}", "project": "app", "ticket": "T-1", "kind": "lesson",
                "status": "confirmed",
                "title": f"Extra padding lesson {i} " + ("y" * 50),
                "claim": ("Upload retries preserve operation identity. " * 15) + f"extra={i}",
                "rationale": "More ticket evidence " * 25,
                "source_ids": ["src-1"], "need_tags": ["upload"],
            }])
        plans = []

        class CountingPlanner:
            def plan(self, task, rules):
                plans.append(task["query"])
                return {"actions": ["implement"], "needs": ["upload"]}

        runs_before = self.store.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        with mock.patch("context_lab.agent_api.provider_flags", return_value={
            "planner": CountingPlanner(), "embeddings": None,
        }):
            view = context(self.store, {
                "project": "app", "ticket": "T-1",
                "query": "upload retry constraints please implement carefully",
            }, budget=500)
        runs_after = self.store.db.execute("SELECT count(*) FROM runs").fetchone()[0]
        self.assertEqual(len(plans), 1)
        self.assertEqual(runs_after - runs_before, 1)
        self.assertEqual(view["run_id"], self.store.db.execute(
            "SELECT id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()[0])
        self.assertLessEqual(view["wire_estimated_tokens"], 500)


if __name__ == "__main__":
    unittest.main()
