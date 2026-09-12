import re
import tempfile
import unittest
from pathlib import Path

from context_lab.knowledge import allocate_ticket, initiate
from context_lab.service import info, scopes
from context_lab.store import Store


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notes = self.root / "Ticket-001"
        self.notes.mkdir()
        (self.notes / "note.md").write_text("# Overview\nScoped note.\n", encoding="utf-8")
        journal = self.notes / "Cl" / "2026-09-12T12-00-00Z"
        journal.mkdir(parents=True)
        (journal / "session.md").write_text("Agent journal prose.", encoding="utf-8")
        self.db = str(self.root / "memory.sqlite3")
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_list_scope_rows_and_scopes_include_baseline_and_counts(self):
        initiate(self.store, "Picnic", "picnic-001", str(self.notes))
        self.store.add_source({"project": "Picnic", "ticket": "", "title": "Baseline", "body": "project only"})
        self.store.add_source({"project": "Picnic", "ticket": "picnic-001", "title": "Ticket", "body": "ticket scoped"})
        sid = self.store.sources(project="Picnic", ticket="picnic-001")[0]["id"]
        self.store.put_memories([{"id": "m1", "project": "Picnic", "ticket": "picnic-001", "kind": "fact",
                                  "title": "T", "claim": "C", "source_ids": [sid], "status": "confirmed"}])
        rows = { (r["project"], r["ticket"]): r for r in self.store.list_scope_rows() }
        self.assertIn(("Picnic", ""), rows)
        self.assertIn(("Picnic", "picnic-001"), rows)
        self.assertEqual(rows[("Picnic", "")]["label"], "Project baseline")
        self.assertEqual(rows[("Picnic", "picnic-001")]["label"], "picnic-001")
        self.assertEqual(rows[("Picnic", "")]["source_count"], 1)
        self.assertEqual(rows[("Picnic", "picnic-001")]["memory_count"], 1)
        self.assertTrue(rows[("Picnic", "picnic-001")]["kb_initialized"])
        payload = scopes(self.store)
        self.assertEqual(payload["by_project"]["Picnic"][0]["ticket"], "")

    def test_scoped_info_excludes_other_tickets(self):
        initiate(self.store, "Picnic", "picnic-001", str(self.notes))
        other = self.store.add_source({"project": "Picnic", "ticket": "picnic-002", "title": "Other", "body": "SECRET"})
        scoped = info(self.store, project="Picnic", ticket="picnic-001")
        self.assertTrue(all(s["ticket"] == "picnic-001" for s in scoped["sources"]))
        self.assertNotIn(other["id"], [s["id"] for s in scoped["sources"]])

    def test_importer_skips_cl_folder(self):
        result = initiate(self.store, "Picnic", "picnic-001", str(self.notes))
        self.assertEqual(result["note_count"], 1)
        bodies = [self.store.source(s["id"])["body"] for s in self.store.sources(project="Picnic", ticket="picnic-001")]
        self.assertTrue(all("Agent journal" not in body for body in bodies))

    def test_allocate_ticket_format(self):
        ticket = allocate_ticket()
        self.assertRegex(ticket, r"^work-\d{8}-\d{6}$")


if __name__ == "__main__":
    unittest.main()
