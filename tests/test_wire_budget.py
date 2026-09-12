"""Wire budget: CompactView must fit budget; inspect keeps traces off the agent wire."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context, inspect_run
from context_lab.engine import compile_context
from context_lab.schemas import format_context, wire_estimated_tokens
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
        full_keys = format_context(packet, detail="inspect")
        # Legacy overshoot shape: prose under budget, inspect/full wire larger.
        self.assertLessEqual(packet["estimated_tokens"], 1200)
        compact = format_context(packet, detail="agent")
        self.assertLessEqual(compact["wire_estimated_tokens"], 1200)
        self.assertLess(compact["wire_estimated_tokens"], wire_estimated_tokens(
            {k: packet[k] for k in ("run_id", "context", "estimated_tokens", "needs", "warnings",
                                    "dependency_gaps", "selected", "trace", "conflicts") if k in packet}
        ))

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


if __name__ == "__main__":
    unittest.main()
