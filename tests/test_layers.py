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

    def _confirmed(self, project, ticket, mid, title, claim, confirm_global=False, kind="constraint"):
        src = {"project": project, "ticket": ticket, "title": title, "body": claim}
        if confirm_global:
            src["confirm_global"] = True
        source = self.store.add_source(src)
        memory = {"id": mid, "project": project, "ticket": ticket, "kind": kind,
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
                        "LAB_WIDE_SECRET use /ponytail when finishing a project",
                        confirm_global=True, kind="standing_rule")
        self._confirmed("course", "", "p1", "Offline first", "BASELINE_SECRET cache offline",
                        kind="standing_rule")
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
        # Policy lane (standing_rule) admits before evidence.
        self.assertLessEqual(selected.index("g1"), selected.index("t1"))

    def test_lab_wide_requires_confirm(self):
        with self.assertRaises(ValueError):
            self.store.add_source({"project": GLOBAL_PROJECT, "title": "x", "body": "y"})
        with self.assertRaises(ValueError):
            self.store.put_memories([{"id": "bad", "project": GLOBAL_PROJECT, "kind": "fact",
                                     "title": "x", "claim": "y", "source_ids": ["missing"]}])

    def test_memory_source_allows_ancestor_layers(self):
        source = self._confirmed(GLOBAL_PROJECT, "", "g2", "Rule", "standing rule",
                                 confirm_global=True, kind="standing_rule")
        got = call(self.store, "memory_source",
                   {"cwd": self.temp.name, "source_id": source["id"], "project": "course", "ticket": "T-1"})
        self.assertEqual(got["id"], source["id"])

    def test_standing_rules_always_included_even_without_lexical_hit(self):
        self._confirmed(GLOBAL_PROJECT, "", "g-stand", "No AI trailers",
                        "Never Co-Authored-By Claude ChatGPT Codex",
                        confirm_global=True, kind="standing_rule")
        self._confirmed("course", "", "p-stand", "Offline cache", "Always prefer offline cache",
                        kind="standing_rule")
        packet = compile_context(self.store, {
            "project": "course", "ticket": "T-9",
            "query": "Commit dashboard lazy-loading optimization for picnic startup",
        }, persist=False)
        selected = [m["id"] for m in packet["selected"]]
        self.assertIn("g-stand", selected)
        self.assertIn("p-stand", selected)
        self.assertIn("Standing rule (reserved policy lane)",
                      next(m["selection_reasons"] for m in packet["selected"] if m["id"] == "g-stand"))

    def test_ordinary_baseline_does_not_crowd_out_ticket_evidence(self):
        self._confirmed("course", "", "p-noise", "Stationery catalog",
                        "Paper sizes envelopes letterhead ink colors " * 30)
        self._confirmed("course", "T-1", "t-crit", "Upload retry identity",
                        "Upload retries preserve operation identity", kind="constraint")
        # Tag ticket memory so targeted need scoring prefers it.
        mem = self.store.memory("t-crit")
        self.store.put_memories([dict(mem, expected_version=mem["version"],
                                     need_tags=["upload"], status="confirmed")])
        packet = compile_context(self.store, {
            "project": "course", "ticket": "T-1",
            "query": "upload retry constraints",
            "needs": ["upload"],
        }, budget=500, persist=False)
        selected = [m["id"] for m in packet["selected"]]
        self.assertIn("t-crit", selected)

    def test_standing_rule_rejects_ticket_scope(self):
        src = self.store.add_source({"project": "course", "ticket": "T-1", "title": "t", "body": "x"})
        with self.assertRaises(ValueError):
            self.store.put_memories([{
                "id": "bad-stand", "project": "course", "ticket": "T-1", "kind": "standing_rule",
                "status": "confirmed", "title": "nope", "claim": "x", "source_ids": [src["id"]],
            }])

    def test_standing_rule_missing_support_fails_closed(self):
        src = self.store.add_source({
            "project": "course", "ticket": "", "title": "t", "body": "must call recall",
        })
        dep_src = self.store.add_source({
            "project": "course", "ticket": "", "title": "dep", "body": "support evidence",
        })
        self.store.put_memories([{
            "id": "sr-dep", "project": "course", "ticket": "", "kind": "lesson",
            "status": "confirmed", "title": "Support", "claim": "support evidence",
            "source_ids": [dep_src["id"]],
        }])
        self.store.put_memories([{
            "id": "sr-orphan", "project": "course", "ticket": "", "kind": "standing_rule",
            "status": "confirmed", "title": "Needs dep", "claim": "Standing needs support",
            "source_ids": [src["id"]], "depends_on": ["sr-dep"],
        }])
        dep = self.store.memory("sr-dep")
        self.store.put_memories([dict(dep, status="retracted", expected_version=dep["version"])])
        with self.assertRaisesRegex(ValueError, "MandatoryPolicyBlocked"):
            compile_context(self.store, {
                "project": "course", "ticket": "T-1", "query": "anything",
            }, persist=False)

    def test_info_lists_inherited(self):
        self._confirmed(GLOBAL_PROJECT, "", "g3", "Rule", "lab rule",
                        confirm_global=True, kind="standing_rule")
        self._confirmed("course", "", "p2", "Base", "project rule")
        payload = info(self.store, project="course", ticket="T-9")
        self.assertEqual(payload["memories"], [])
        self.assertEqual({m["id"] for m in payload["inherited_memories"]}, {"g3", "p2"})
        self.assertIn(GLOBAL_PROJECT, payload["projects"])


if __name__ == "__main__":
    unittest.main()
