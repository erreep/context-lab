"""Cross-ticket promotion with scoped summary evidence and opaque provenance."""
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import compile_context
from context_lab.store import Store


class PromoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))
        self.src = self.store.add_source({
            "project": "app", "ticket": "T-1", "title": "incident",
            "body": "Upload retries must preserve operation identity under duplicate delivery.",
        })
        self.store.put_memories([{
            "id": "mem-ticket", "project": "app", "ticket": "T-1", "kind": "lesson",
            "status": "confirmed", "title": "Retry identity",
            "claim": "Upload retries preserve operation identity",
            "source_ids": [self.src["id"]], "need_tags": ["upload"],
        }])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_promote_creates_baseline_candidate_without_ticket_sources(self):
        result = self.store.promote("mem-ticket")
        mem = result["memory"]
        self.assertEqual(mem["ticket"], "")
        self.assertEqual(mem["status"], "candidate")
        self.assertEqual(mem["source_ids"], [result["summary_source"]["id"]])
        self.assertNotIn(self.src["body"], result["summary_source"]["body"])
        self.assertIn("mem-ticket", result["summary_source"]["body"])
        # Baseline cannot cite ticket source directly.
        with self.assertRaises(ValueError):
            self.store.put_memories([{
                "id": "bad", "project": "app", "ticket": "", "kind": "lesson",
                "status": "candidate", "title": "x", "claim": "y",
                "source_ids": [self.src["id"]],
            }])

    def test_promote_idempotent(self):
        first = self.store.promote("mem-ticket")
        second = self.store.promote("mem-ticket")
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["memory"]["id"], second["memory"]["id"])

    def test_sibling_ticket_cannot_read_origin_notes_via_promotion(self):
        result = self.store.promote("mem-ticket")
        confirmed = dict(result["memory"], status="confirmed", expected_version=result["memory"]["version"])
        self.store.put_memories([confirmed])
        packet = compile_context(self.store, {
            "project": "app", "ticket": "T-2", "query": "upload retry", "needs": ["upload"],
        }, persist=False)
        blob = str(packet)
        self.assertNotIn(self.src["body"], blob)
        self.assertNotIn("T-1", [m.get("ticket") for m in packet["selected"]])


if __name__ == "__main__":
    unittest.main()
