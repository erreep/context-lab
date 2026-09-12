"""Lab-wide Obsidian vault binding on first OS touch."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import initiate
from context_lab.knowledge import require_journal_available
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
        self.assertIn("No Obsidian vault found", result["obsidian"]["reason"])
        self.assertIsNone(self.store.knowledge_base("app", "T-1"))

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_decline_vault_allows_empty_ticket_and_blocks_journal(self, _):
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["obsidian"]["journaling"], "unavailable")
        self.assertIn("journaling is not available", result["obsidian"]["reason"])
        with self.assertRaises(ValueError):
            require_journal_available(self.store)

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_bind_vault_is_lab_wide_across_projects(self, _):
        first = initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": str(self.vault)})
        self.assertEqual(first["status"], "initialized")
        self.assertEqual(first["obsidian"]["journaling"], "available")
        self.assertFalse(first["obsidian"]["auto_detected"])
        self.assertEqual(require_journal_available(self.store), self.vault.resolve())

        # Other project reuses lab vault without re-asking.
        second = initiate(self.store, "other", "T-9", knowledge={"mode": "auto"})
        self.assertEqual(second["status"], "initialized")
        self.assertEqual(second["obsidian"]["journaling"], "available")
        self.assertEqual(second["vault_path"], str(self.vault.resolve()))

    @patch("context_lab.knowledge.discover_obsidian_vaults")
    def test_auto_detect_binds_and_notifies(self, discover):
        discover.return_value = [{"path": str(self.vault.resolve()), "ts": 99, "open": True}]
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "empty"})
        self.assertEqual(result["status"], "initialized")
        self.assertTrue(result["obsidian"]["auto_detected"])
        self.assertIn("Auto-detected", result["obsidian"]["reason"])
        self.assertEqual(result["vault_path"], str(self.vault.resolve()))

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_attach_vault_after_decline(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        later = initiate(self.store, "app", "", knowledge={"vault": str(self.vault)})
        self.assertEqual(later["status"], "vault_configured")
        self.assertEqual(later["obsidian"]["journaling"], "available")


if __name__ == "__main__":
    unittest.main()
