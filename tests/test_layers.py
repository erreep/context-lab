import json
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import compile_context
from context_lab.mcp import call
from context_lab.service import info
from context_lab.store import GLOBAL_PROJECT, Store, scope_layers


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _confirmed(self, project, ticket, mid, title, claim, confirm_global=False):
        src = {"project": project, "ticket": ticket, "title": title, "body": claim}
        if confirm_global:
            src["confirm_global"] = True
        source = self.store.add_source(src)
        memory = {"id": mid, "project": project, "ticket": ticket, "kind": "constraint",
                  "status": "confirmed", "title": title, "claim": claim, "source_ids": [source["id"]]}
        if confirm_global:
            memory["confirm_global"] = True
        self.store.put_memories([memory])
        return source

    def test_scope_layers_order(self):
        self.assertEqual(scope_layers({"project": "course", "ticket": "T-1"}),
                         [(GLOBAL_PROJECT, ""), ("course", ""), ("course", "T-1")])
        self.assertEqual(scope_layers({"project": GLOBAL_PROJECT, "ticket": ""}),
                         [(GLOBAL_PROJECT, "")])

    def test_ticket_inherits_baseline_and_lab_wide_but_not_siblings(self):
        self._confirmed(GLOBAL_PROJECT, "", "g1", "Finish with ponytail",
                        "LAB_WIDE_SECRET use /ponytail when finishing a project", confirm_global=True)
        self._confirmed("course", "", "p1", "Offline first", "BASELINE_SECRET cache offline")
        self._confirmed("course", "T-1", "t1", "Pool size", "TICKET_SECRET pool=8")
        self._confirmed("course", "T-2", "t2", "Other ticket", "SIBLING_SECRET never leak")
        packet = compile_context(self.store, {"project": "course", "ticket": "T-1",
                                              "query": "pool size finishing project offline"}, persist=False)
        blob = json.dumps(packet)
        self.assertIn("LAB_WIDE_SECRET", blob)
        self.assertIn("BASELINE_SECRET", blob)
        self.assertIn("TICKET_SECRET", blob)
        self.assertNotIn("SIBLING_SECRET", blob)
        self.assertNotIn("t2", [t["id"] for t in packet["trace"]])
        selected = [t["id"] for t in packet["selected"]]
        self.assertLessEqual(selected.index("g1"), selected.index("t1"))

    def test_lab_wide_requires_confirm(self):
        with self.assertRaises(ValueError):
            self.store.add_source({"project": GLOBAL_PROJECT, "title": "x", "body": "y"})
        with self.assertRaises(ValueError):
            self.store.put_memories([{"id": "bad", "project": GLOBAL_PROJECT, "kind": "fact",
                                     "title": "x", "claim": "y", "source_ids": ["missing"]}])

    def test_memory_source_allows_ancestor_layers(self):
        source = self._confirmed(GLOBAL_PROJECT, "", "g2", "Rule", "standing rule", confirm_global=True)
        got = call(self.store, "memory_source",
                   {"source_id": source["id"], "project": "course", "ticket": "T-1"})
        self.assertEqual(got["id"], source["id"])

    def test_info_lists_inherited(self):
        self._confirmed(GLOBAL_PROJECT, "", "g3", "Rule", "lab rule", confirm_global=True)
        self._confirmed("course", "", "p2", "Base", "project rule")
        payload = info(self.store, project="course", ticket="T-9")
        self.assertEqual(payload["memories"], [])
        self.assertEqual({m["id"] for m in payload["inherited_memories"]}, {"g3", "p2"})
        self.assertIn(GLOBAL_PROJECT, payload["projects"])


if __name__ == "__main__":
    unittest.main()
