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

    def test_http_hide_and_info_trash(self):
        from context_lab.service import dispatch, info
        src, mem = self._seed()
        out = dispatch(self.store, "hide", {"kind": "memory", "id": mem["id"]})
        self.assertEqual(out["tombstones"][0]["state"], "active")
        trash = info(self.store, "app", "T-1")["trash"]
        self.assertTrue(any(t["id"] == mem["id"] for t in trash))
        dispatch(self.store, "unhide", {"kind": "memory", "id": mem["id"]})
        self.assertFalse(info(self.store, "app", "T-1")["trash"])

    def test_run_redacts_unless_forensic(self):
        _src, mem = self._seed()
        packet = compile_context(
            self.store,
            {"project": "app", "ticket": "T-1", "query": "soft-delete", "as_of": "2026-01-01"},
            persist=True,
        )
        self.assertIn("m-live", {m["id"] for m in packet["selected"]})
        self.store.hide(MemoryRef("m-live"), actor=OperatorActor())
        live = self.store.run(packet["run_id"])
        self.assertNotIn("m-live", {m["id"] for m in live["selected"]})
        frozen = self.store.run(packet["run_id"], forensic=True)
        self.assertIn("m-live", {m["id"] for m in frozen["selected"]})

    def test_note_cascade_shares_group_and_restores_pair(self):
        from context_lab.visibility import DocumentRef

        sid = "kb-src-note"
        self.store.add_source({
            "id": sid,
            "project": "app",
            "ticket": "T-1",
            "title": "note.md",
            "body": "Note body for cascade.",
        })
        self.store.put_knowledge_base({
            "project": "app",
            "ticket": "T-1",
            "documents": [{
                "id": "doc-note-1",
                "kind": "document",
                "status": "indexed",
                "title": "note.md",
                "claim": "Note body for cascade.",
                "source_ids": [sid],
            }],
        })
        rows = self.store.hide(DocumentRef("doc-note-1"), actor=OperatorActor())
        kinds = {(t.kind, t.id) for t in rows}
        self.assertIn(("document", "doc-note-1"), kinds)
        self.assertIn(("source", sid), kinds)
        self.assertEqual(len({t.group_id for t in rows}), 1)
        self.assertTrue(self.store.visibility().source_hidden(sid))
        self.assertEqual(len(self.store.documents("app", "T-1")), 0)
        self.store.unhide(SourceRef(sid), actor=OperatorActor())
        self.assertFalse(self.store.visibility().source_hidden(sid))
        self.assertEqual(len(self.store.documents("app", "T-1")), 1)

    def test_mcp_delete_hides_exact_scope(self):
        from context_lab.agent_api import delete
        from context_lab.schemas import AgentError

        _src, mem = self._seed()
        out = delete(self.store, "memory", mem["id"], bound=("app", "T-1"))
        self.assertEqual(out["tombstones"][0]["state"], "active")
        with self.assertRaises(AgentError):
            delete(self.store, "memory", mem["id"], bound=("other", "T-9"))


if __name__ == "__main__":
    unittest.main()
