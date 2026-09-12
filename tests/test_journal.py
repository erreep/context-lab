"""memory_journal: durable ticket folder write + immediate index."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context, initiate, journal
from context_lab.knowledge import index_ticket_file
from context_lab.store import Store


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notes = self.root / "ticket-notes"
        self.notes.mkdir()
        (self.notes / "seed.md").write_text("# Seed\nInitial note.\n", encoding="utf-8")
        self.store = Store(str(self.root / "memory.sqlite3"))
        with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
            initiate(
                self.store, "app", "T-1",
                knowledge={"mode": "import", "path": str(self.notes), "vault": "none"},
            )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_journal_write_index_idempotent_and_retrievable(self):
        frozen = datetime(2026, 9, 12, 15, 30, 0, tzinfo=timezone.utc)
        words = "We chose cobalt-only pigment for AlphaWidget shipping."
        with patch("context_lab.agent_api.datetime") as dt:
            dt.now.return_value = frozen
            first = journal(
                self.store, "app", "T-1", "decision",
                "Alpha pigment decision", words,
            )
            second = journal(
                self.store, "app", "T-1", "decision",
                "Alpha pigment decision", words,
            )
        self.assertTrue(Path(first["path"]).is_file())
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(first["index"]["status"], "indexed")
        self.assertEqual(second["index"]["status"], "already_indexed")
        sources = [s for s in self.store.sources("app", "T-1") if "journal/" in s["title"]]
        self.assertEqual(len(sources), 1)
        again = index_ticket_file(self.store, "app", "T-1", first["path"])
        self.assertEqual(again["status"], "already_indexed")
        view = context(self.store, {
            "project": "app", "ticket": "T-1",
            "query": "What pigment decision did we make for AlphaWidget?",
        })
        blob = view.get("context", "") + str(view.get("picks"))
        self.assertIn("cobalt", blob.lower())


if __name__ == "__main__":
    unittest.main()
