"""Shared server config, local-only hosts, and unique ticket ids."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context
from context_lab.knowledge import allocate_ticket
from context_lab.provider import ModelEndpoint, assert_local_host
from context_lab.store import Store


class ServerConfigTests(unittest.TestCase):
    def test_allocate_ticket_unique_within_second(self):
        ids = {allocate_ticket() for _ in range(20)}
        self.assertEqual(len(ids), 20)
        for ticket in ids:
            self.assertRegex(ticket, r"^work-\d{8}-\d{6}-[0-9a-f]{4}$")

    def test_local_only_rejects_public_host(self):
        with self.assertRaises(ValueError):
            assert_local_host("https://api.openai.com/v1")
        with self.assertRaises(ValueError):
            ModelEndpoint(base_url="https://example.com/v1", model="x", local_only=True)

    def test_local_only_accepts_loopback(self):
        endpoint = ModelEndpoint(base_url="http://127.0.0.1:12345/v1", model="x", local_only=True)
        self.assertTrue(endpoint.base.startswith("http://127.0.0.1"))

    @patch.dict(os.environ, {
        "CONTEXT_LAB_BASE_URL": "http://127.0.0.1:9/v1",
        "CONTEXT_LAB_MODEL": "m",
        "CONTEXT_LAB_EMBEDDING_MODEL": "",
        "CONTEXT_LAB_LOCAL_ONLY": "1",
    }, clear=False)
    def test_mcp_context_wires_planner_from_env(self):
        temp = tempfile.TemporaryDirectory()
        store = Store(str(Path(temp.name) / "memory.sqlite3"))
        try:
            src = store.add_source({"project": "app", "ticket": "", "title": "t", "body": "Brand colors are blue"})
            store.put_memories([{
                "id": "C-1", "project": "app", "ticket": "", "kind": "constraint", "status": "confirmed",
                "title": "Brand", "claim": "Brand colors are blue", "source_ids": [src["id"]],
            }])
            with patch("context_lab.provider.ModelEndpoint.plan", return_value={"actions": [], "needs": []}):
                view = context(store, {"project": "app", "query": "Change button color"}, detail="agent")
            self.assertIn("run_id", view)
        finally:
            store.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
