"""Soft-delete visibility ledger: hide vanishes from recall; restore same id."""
import tempfile
import unittest
from pathlib import Path

from context_lab.agent_api import source as agent_source
from context_lab.engine import compile_context
from context_lab.schemas import AgentError
from context_lab.store import Store
from context_lab.visibility import MemoryRef, OperatorActor, SourceRef, strip_visibility_keys


class SoftDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _seed(self):
        src = self.store.add_source({
            "project": "app",
            "ticket": "T-1",
            "title": "obs",
            "body": "Evidence body for soft-delete.",
        })
        mem = self.store.put_memories([{
            "id": "m-live",
            "project": "app",
            "ticket": "T-1",
            "kind": "lesson",
            "status": "confirmed",
            "title": "Keep me",
            "claim": "Evidence body for soft-delete.",
            "source_ids": [src["id"]],
            "valid_from": "2020-01-01",
        }])[0]
        return src, mem

    def test_hide_compile_vanish_unhide_same_id(self):
        src, mem = self._seed()
        before = compile_context(
            self.store,
            {"project": "app", "ticket": "T-1", "query": "soft-delete", "as_of": "2026-01-01"},
            persist=False,
        )
        self.assertIn("m-live", {m["id"] for m in before["selected"]})

        hidden = self.store.hide(
            MemoryRef("m-live"),
            actor=OperatorActor(),
        )
        self.assertEqual(hidden[0].state, "active")
        self.assertEqual(src["body"], self.store.source(src["id"])["body"])

        after = compile_context(
            self.store,
            {"project": "app", "ticket": "T-1", "query": "soft-delete", "as_of": "2026-01-01"},
            persist=False,
        )
        self.assertNotIn("m-live", {m["id"] for m in after["selected"]})
        self.assertNotIn("m-live", {t["id"] for t in after["trace"]})
        self.assertEqual(self.store.memory("m-live")["status"], "confirmed")

        self.store.unhide(MemoryRef("m-live"), actor=OperatorActor())
        restored = compile_context(
            self.store,
            {"project": "app", "ticket": "T-1", "query": "soft-delete", "as_of": "2026-01-01"},
            persist=False,
        )
        self.assertIn("m-live", {m["id"] for m in restored["selected"]})

    def test_hidden_source_fails_agent_read_and_keeps_body(self):
        src, _mem = self._seed()
        body = src["body"]
        self.store.hide(SourceRef(src["id"]), actor=OperatorActor())
        with self.assertRaises(AgentError):
            agent_source(self.store, src["id"], "app", "T-1")
        self.assertEqual(self.store.source(src["id"])["body"], body)

    def test_strip_visibility_keys(self):
        cleaned = strip_visibility_keys({
            "id": "x",
            "deleted": True,
            "deleted_at": "now",
            "hidden": True,
            "title": "t",
        })
        self.assertEqual(cleaned, {"id": "x", "title": "t"})

    def test_lab_hide_needs_confirm_global(self):
        src = self.store.add_source({
            "project": "__global__",
            "title": "lab",
            "body": "Lab evidence.",
            "confirm_global": True,
        })
        self.store.put_memories([{
            "id": "m-lab",
            "project": "__global__",
            "kind": "standing_rule",
            "status": "confirmed",
            "title": "Lab rule",
            "claim": "Lab evidence.",
            "source_ids": [src["id"]],
            "confirm_global": True,
            "valid_from": "2020-01-01",
        }])
        with self.assertRaises(ValueError):
            self.store.hide(MemoryRef("m-lab"), actor=OperatorActor(confirm_global=False))
        rows = self.store.hide(MemoryRef("m-lab"), actor=OperatorActor(confirm_global=True))
        self.assertEqual(rows[0].state, "active")


if __name__ == "__main__":
    unittest.main()
