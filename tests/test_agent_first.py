"""Agent-first surface: KnowledgeChoice, full context packet, propose schema."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context, initiate, propose
from context_lab.mcp import TOOLS, call
from context_lab.store import Store


class AgentFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_knowledge_auto_starts_empty_without_qna(self, _):
        result = initiate(self.store, "app", "T-1", knowledge={"mode": "auto", "vault": "none"})
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["obsidian"]["journaling"], "unavailable")
        self.assertEqual(initiate(self.store, "app", "T-1", knowledge={"mode": "auto"})["status"], "already_initialized")
        self.assertEqual(initiate(self.store, "app", "T-1", knowledge={"mode": "reuse"})["status"], "already_initialized")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_legacy_initiate_still_asks_for_kb(self, _):
        initiate(self.store, "app", "", knowledge={"vault": "none"})
        self.assertEqual(initiate(self.store, "app", "T-2")["status"], "needs_knowledge_base")

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_context_default_is_compact(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        src = self.store.add_source({"project": "app", "ticket": "T-1", "title": "t", "body": "Brand colors are blue"})
        self.store.put_memories([{
            "id": "C-1", "project": "app", "ticket": "T-1", "kind": "constraint", "status": "confirmed",
            "title": "Brand", "claim": "Brand colors are blue", "source_ids": [src["id"]],
        }])
        # Seed an excluded candidate so inspect can see it; agent wire must not.
        self.store.put_memories([{
            "id": "C-cand", "project": "app", "ticket": "T-1", "kind": "lesson", "status": "candidate",
            "title": "Secret candidate title", "claim": "Should not leak", "source_ids": [src["id"]],
        }])
        compact = context(self.store, {"project": "app", "ticket": "T-1", "query": "Change button color"})
        self.assertNotIn("selected", compact)
        self.assertNotIn("trace", compact)
        self.assertIn("picks", compact)
        self.assertIn("wire_estimated_tokens", compact)
        self.assertLessEqual(compact["wire_estimated_tokens"], 1200)
        self.assertNotIn("Secret candidate title", json.dumps(compact))
        inspect = context(self.store, {"project": "app", "ticket": "T-1", "query": "Change button color"}, detail="inspect")
        self.assertIn("selected", inspect)
        self.assertIn("trace", inspect)
        self.assertIn("Secret candidate title", json.dumps(inspect))
        prose = context(self.store, {"project": "app", "ticket": "T-1", "query": "Change button color"}, detail="prose")
        self.assertNotIn("selected", prose)
        self.assertIn("context", prose)

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_propose_mints_id_and_activation_hint(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        src = self.store.add_source({"project": "app", "ticket": "T-1", "title": "obs", "body": "Ship structured errors"})
        out = propose(self.store, [{
            "project": "app", "ticket": "T-1", "kind": "lesson",
            "title": "Structured errors", "claim": "Ship structured errors", "source_ids": [src["id"]],
        }])
        self.assertTrue(out["memories"][0]["id"].startswith("mem-"))
        self.assertEqual(out["memories"][0]["status"], "candidate")
        self.assertEqual(out["activation"]["via"], "ui")
        self.assertEqual(out["activation"]["pending_candidates"], 1)

    @patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[])
    def test_mcp_propose_schema_and_structured_error(self, _):
        initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        src = self.store.add_source({"project": "app", "ticket": "T-1", "title": "obs", "body": "evidence"})
        call(self.store, "memory_propose", {"memories": [{
            "id": "dup", "project": "app", "ticket": "T-1", "kind": "lesson",
            "title": "a", "claim": "evidence", "source_ids": [src["id"]],
        }]})
        with self.assertRaises(Exception) as ctx:
            call(self.store, "memory_propose", {"memories": [{
                "id": "dup", "project": "app", "ticket": "T-1", "kind": "lesson",
                "title": "a", "claim": "evidence", "source_ids": [src["id"]],
            }]})
        self.assertEqual(ctx.exception.code, "memory_exists_revise_via_ui")
        schema = next(t for t in TOOLS if t["name"] == "memory_propose")
        self.assertIn("kind", schema["inputSchema"]["properties"]["memories"]["items"]["properties"])


if __name__ == "__main__":
    unittest.main()
