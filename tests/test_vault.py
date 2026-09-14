"""Lab-wide Obsidian vault binding and journal readiness."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import initiate, journal
from context_lab.knowledge import NEXT_STEP, Readiness, journal_home, require_journal_home
from context_lab.store import Store


class VaultBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "Vault"
        self.vault.mkdir()
        self.store = Store(str(self.root / "memory.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_first_os_touch_without_vault_is_blocked(self, _):
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "auto"})
        self.assertEqual(result["status"], "needs_obsidian_vault")
        self.assertEqual(result["obsidian"]["journaling"], "unavailable")
        self.assertEqual(result["obsidian"]["next"], NEXT_STEP["unavailable"])
        self.assertIsNone(self.store.knowledge_base("app", "T-1"))

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_decline_vault_allows_empty_ticket_and_blocks_journal(self, _):
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["obsidian"]["journaling"], "unavailable")
        self.assertIsNone(self.store.knowledge_base("app", "T-1")["path"])
        with self.assertRaises(ValueError) as ctx:
            require_journal_home(self.store, "app", "T-1")
        self.assertEqual(str(ctx.exception), NEXT_STEP["unavailable"])

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_bind_vault_provisions_ticket_folder(self, _):
        first = initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": str(self.vault)})
        self.assertEqual(first["status"], "initialized")
        self.assertEqual(first["obsidian"]["journaling"], "ready")
        notes = Path(first["obsidian"]["notes"])
        self.assertTrue(notes.is_dir())
        self.assertEqual(notes, (self.vault / "Context Lab" / "app" / "T-1").resolve())
        self.assertTrue((notes / "README.md").is_file())
        self.assertIn("announce", first["obsidian"])
        home, created = require_journal_home(self.store, "app", "T-1")
        self.assertEqual(home.readiness, Readiness.ready)
        self.assertEqual(home.notes, notes)
        self.assertFalse(created)

        second = initiate(self.store, "other", "T-9", knowledge={"mode": "auto"})
        self.assertEqual(second["status"], "initialized")
        self.assertEqual(second["obsidian"]["journaling"], "ready")
        self.assertTrue((self.vault / "Context Lab" / "other" / "T-9" / "README.md").is_file())

    @patch("context_lab.knowledge.discover_obsidian_vaults")
    def test_auto_detect_binds_and_notifies(self, discover):
        discover.return_value = [{"path": str(self.vault.resolve()), "ts": 99, "open": True}]
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "empty"})
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["obsidian"]["journaling"], "ready")
        self.assertIn("Auto-bound vault", result["obsidian"]["announce"])
        self.assertIn("Context Lab/app/T-1", result["obsidian"]["announce"])

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_attach_vault_after_decline(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        later = initiate(self.store, "app", "", knowledge={"vault": str(self.vault)})
        self.assertEqual(later["status"], "vault_configured")
        self.assertEqual(later["obsidian"]["journaling"], "vault_only")
        self.assertEqual(later["obsidian"]["next"], NEXT_STEP["vault_only_no_ticket"])
        healed = initiate(self.store, "app", "T-1", knowledge={"mode": "auto"})
        self.assertEqual(healed["obsidian"]["journaling"], "ready")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_import_with_declined_vault_is_ready(self, _):
        notes = self.root / "imported"
        notes.mkdir()
        (notes / "seed.md").write_text("# Seed\nnote\n", encoding="utf-8")
        result = initiate(
            self.store, "app", "T-17",
            knowledge={"mode": "import", "path": str(notes), "vault": "none"},
        )
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["obsidian"]["journaling"], "ready")
        self.assertEqual(Path(result["obsidian"]["notes"]), notes.resolve())
        written = journal(self.store, "app", "T-17", "plan", "Imported plan", "Works without a vault.")
        self.assertEqual(written["path"], "journal/plan-imported-plan.md")
        self.assertTrue((notes / "journal" / "plan-imported-plan.md").is_file())

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_no_ticket_with_bound_vault_is_vault_only(self, _):
        result = initiate(self.store, "app", "", knowledge={"vault": str(self.vault)})
        self.assertEqual(result["obsidian"]["journaling"], "vault_only")
        self.assertEqual(journal_home(self.store, "app", "").readiness, Readiness.vault_only)


if __name__ == "__main__":
    unittest.main()
