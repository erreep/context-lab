"""Cross-ticket promotion with scoped summary evidence and opaque provenance."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import compile_context
from context_lab.store import Store


def promo_ids(origin_id, project, claim):
    key = hashlib.sha256(f"{origin_id}|{project}|{claim}".encode()).hexdigest()[:16]
    return "src-promo-" + key, "mem-promo-" + key


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

    def test_promote_preserves_validity_and_rejects_ticket_deps(self):
        dep_src = self.store.add_source({
            "project": "app", "ticket": "", "title": "base dep", "body": "baseline support",
        })
        self.store.put_memories([{
            "id": "mem-dep", "project": "app", "ticket": "", "kind": "lesson",
            "status": "confirmed", "title": "Dep", "claim": "baseline support",
            "source_ids": [dep_src["id"]],
        }])
        self.store.put_memories([{
            "id": "mem-limited", "project": "app", "ticket": "T-1", "kind": "lesson",
            "status": "confirmed", "title": "Timed", "claim": "Expires soon",
            "source_ids": [self.src["id"]], "depends_on": ["mem-dep"],
            "valid_from": "2020-01-01", "valid_until": "2020-06-01",
        }])
        result = self.store.promote("mem-limited")
        mem = result["memory"]
        self.assertEqual(mem["valid_until"], "2020-06-01")
        self.assertEqual(mem["depends_on"], ["mem-dep"])
        self.store.put_memories([{
            "id": "mem-bad-dep", "project": "app", "ticket": "T-1", "kind": "lesson",
            "status": "confirmed", "title": "Bad", "claim": "needs ticket dep",
            "source_ids": [self.src["id"]], "depends_on": ["mem-ticket"],
        }])
        with self.assertRaisesRegex(ValueError, "ticket-scoped depends_on"):
            self.store.promote("mem-bad-dep")

    def test_failed_promote_leaves_no_summary_and_allows_retry(self):
        claim = "needs ticket dep then fixed"
        self.store.put_memories([{
            "id": "mem-bad-dep", "project": "app", "ticket": "T-1", "kind": "lesson",
            "status": "confirmed", "title": "Bad", "claim": claim,
            "source_ids": [self.src["id"]], "depends_on": ["mem-ticket"],
        }])
        with self.assertRaisesRegex(ValueError, "ticket-scoped depends_on"):
            self.store.promote("mem-bad-dep")
        src_id, mem_id = promo_ids("mem-bad-dep", "app", claim)
        self.assertIsNone(self.store.source(src_id))
        self.assertIsNone(self.store.memory(mem_id))

        dep_src = self.store.add_source({
            "project": "app", "ticket": "", "title": "base", "body": "baseline support",
        })
        self.store.put_memories([{
            "id": "mem-dep", "project": "app", "ticket": "", "kind": "lesson",
            "status": "confirmed", "title": "Dep", "claim": "baseline support",
            "source_ids": [dep_src["id"]],
        }])
        bad = self.store.memory("mem-bad-dep")
        # Bump origin version so a leftover summary from the old order would conflict.
        self.store.put_memories([dict(
            bad, depends_on=["mem-dep"], expected_version=bad["version"],
        )])
        result = self.store.promote("mem-bad-dep")
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["memory"]["depends_on"], ["mem-dep"])
        self.assertIsNotNone(self.store.source(src_id))


if __name__ == "__main__":
    unittest.main()
