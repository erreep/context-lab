"""memory_journal: provision, short names, slim return, document lane."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context, initiate, journal
from context_lab.engine import compile_context
from context_lab.knowledge import index_ticket_file, journal_digest
from context_lab.schemas import AgentError, compact_record_title
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
        words = "We chose cobalt-only pigment for AlphaWidget shipping."
        first = journal(self.store, "app", "T-1", "decision", "Alpha pigment decision", words)
        second = journal(self.store, "app", "T-1", "decision", "Alpha pigment decision", words)
        self.assertEqual(first["path"], "journal/decision-alpha-pigment-decision.md")
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(first["kind"], "decision")
        self.assertEqual(first["status"], "indexed")
        self.assertEqual(second["status"], "already_indexed")
        self.assertIn("source_id", first)
        self.assertNotIn("project", first)
        self.assertNotIn("ticket", first)
        self.assertNotIn("relative_path", first)
        self.assertFalse(Path(first["path"]).is_absolute())
        dest = self.notes / first["path"]
        self.assertTrue(dest.is_file())
        self.assertIn("digest:", dest.read_text(encoding="utf-8"))
        self.assertEqual(len(list((self.notes / "journal").glob("*.md"))), 1)
        sources = [s for s in self.store.sources("app", "T-1") if "journal/" in s["title"]]
        self.assertEqual(len(sources), 1)
        again = index_ticket_file(self.store, "app", "T-1", dest)
        self.assertEqual(again["status"], "already_indexed")
        view = context(self.store, {
            "project": "app", "ticket": "T-1",
            "query": "What pigment decision did we make for AlphaWidget?",
        })
        blob = view.get("context", "") + str(view.get("picks"))
        self.assertIn("cobalt", blob.lower())
        for pick in view["picks"]:
            self.assertLessEqual(len(pick["title"]), 64)
            self.assertNotIn(" · ", pick["title"])

    def test_content_collision_uses_digest_suffix(self):
        journal(self.store, "app", "T-1", "decision", "Same title", "First body.")
        other = journal(self.store, "app", "T-1", "decision", "Same title", "Second body, different.")
        digest = journal_digest("decision", "Same title", "Second body, different.")
        self.assertEqual(other["path"], f"journal/decision-same-title-{digest}.md")
        self.assertEqual(len(list((self.notes / "journal").glob("*.md"))), 2)

    def test_retry_finds_legacy_digest_name(self):
        title, body = "Legacy name", "Same bytes as the retry."
        digest = journal_digest("progress", title, body)
        journal_dir = self.notes / "journal"
        journal_dir.mkdir()
        legacy = journal_dir / f"progress-20260912T153000Z-legacy-name-{digest}.md"
        legacy.write_text(
            f"---\nkind: progress\nproject: app\nticket: T-1\ndigest: {digest}\n---\n\n"
            f"# {title}\n\n{body}\n",
            encoding="utf-8",
        )
        result = journal(self.store, "app", "T-1", "progress", title, body)
        self.assertEqual(result["path"], legacy.relative_to(self.notes).as_posix())
        self.assertEqual(len(list(journal_dir.glob("*.md"))), 1)

    def test_compact_document_title_uses_heading(self):
        rec = {
            "kind": "document",
            "title": "old path heading",
            "heading": "Alpha pigment decision",
            "path": "journal/decision-alpha-pigment-decision.md",
        }
        self.assertEqual(compact_record_title(rec), "Alpha pigment decision")
        rec["heading"] = "H" * 80
        self.assertEqual(len(compact_record_title(rec)), 64)


class JournalProvisionTests(unittest.TestCase):
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
    def test_auto_bound_vault_journals_without_import(self, _):
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "auto", "vault": str(self.vault)})
        self.assertEqual(result["obsidian"]["journaling"], "ready")
        notes = Path(result["obsidian"]["notes"])
        self.assertTrue((notes / "README.md").is_file())
        written = journal(self.store, "app", "T-1", "handoff", "Pigment run status", "Cobalt only.")
        self.assertEqual(written["status"], "indexed")
        self.assertEqual(written["path"], "journal/handoff-pigment-run-status.md")
        self.assertTrue((notes / written["path"]).is_file())

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_empty_plus_none_cannot_journal(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        with self.assertRaises(AgentError) as ctx:
            journal(self.store, "app", "T-1", "plan", "No folder", "Should fail.")
        self.assertEqual(ctx.exception.code, "journal_not_ready")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_taken_folder_stays_vault_only(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "auto", "vault": str(self.vault)})
        taken = (self.vault / "Context Lab" / "app" / "T-2").resolve()
        other = self.store.knowledge_base("app", "T-1")
        other["path"] = str(taken)
        self.store.put_knowledge_base(other)
        result = initiate(self.store, "app", "T-2", knowledge={"mode": "auto"})
        self.assertEqual(result["obsidian"]["journaling"], "vault_only")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_unsafe_ticket_name_is_vault_only(self, _):
        result = initiate(self.store, "app", "con", knowledge={"mode": "empty", "vault": str(self.vault)})
        self.assertEqual(result["obsidian"]["journaling"], "vault_only")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_document_lane_does_not_displace_standing_rules(self, _):
        initiate(self.store, "app", "T-8", knowledge={"mode": "auto", "vault": str(self.vault)})
        journal(
            self.store, "app", "T-8", "decision", "Alpha pigment decision",
            "We chose cobalt-only pigment for AlphaWidget shipping. " * 8,
        )
        src_rule = self.store.add_source({
            "project": "app", "ticket": "", "title": "rule-src",
            "body": "Always confirm pigment stock before shipping.",
        })
        src_mem = self.store.add_source({
            "project": "app", "ticket": "T-8", "title": "mem-src",
            "body": "Cobalt pigment is the shipping constraint.",
        })
        self.store.put_memories([{
            "id": "SR-pigment", "project": "app", "ticket": "", "kind": "standing_rule",
            "status": "confirmed", "title": "Confirm pigment stock",
            "claim": "Always confirm pigment stock before shipping.",
            "source_ids": [src_rule["id"]],
        }])
        self.store.put_memories([{
            "id": "M-cobalt", "project": "app", "ticket": "T-8", "kind": "constraint",
            "status": "confirmed", "title": "Cobalt shipping",
            "claim": "Cobalt pigment is the shipping constraint.",
            "source_ids": [src_mem["id"]],
        }])
        task = {
            "project": "app", "ticket": "T-8",
            "query": "What pigment decision and standing stock rule apply to AlphaWidget?",
        }
        tight = compile_context(self.store, task, budget=800, persist=False)
        kinds = [m["kind"] for m in tight["selected"]]
        self.assertIn("standing_rule", kinds)
        self.assertIn("constraint", kinds)
        self.assertNotIn("document", kinds)
        wide = compile_context(self.store, task, budget=2000, persist=False)
        self.assertIn("document", [m["kind"] for m in wide["selected"]])


class UserFacingCopyTests(unittest.TestCase):
    def test_no_user_facing_cl_slash(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "plugins/context-lab/skills/context-lab/SKILL.md",
            "plugins/context-lab/skills/context-lab/gates.md",
            "README.md",
            "context_lab/mcp.py",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("Cl/", text, relative)


if __name__ == "__main__":
    unittest.main()
