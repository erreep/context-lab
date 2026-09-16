import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from context_lab.engine import ROOT, STRATEGIES, compile_context, estimated_tokens
from context_lab.knowledge import initiate
from context_lab.mcp import call, serve_mcp
from context_lab.store import Store


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notes = self.root / "Python Course Notes"
        self.notes.mkdir()
        self.note = self.notes / "Architecture.md"
        self.note.write_text("# Connection pooling\nReuse PostgreSQL connections with a bounded pool.\n\n"
                             "# Testing\nVerify connection cleanup after exceptions.\n", encoding="utf-8")
        self.db = str(self.root / "memory.sqlite3")
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_setup_persists_and_repeated_calls_do_not_read_notes(self):
        self.assertEqual(initiate(self.store, "course", "T-123")["status"], "needs_knowledge_base")
        self.assertIsNone(self.store.knowledge_base("course", "T-123"))
        original = self.note.read_bytes()
        created = initiate(self.store, "course", "T-123", str(self.notes))
        self.assertEqual(created["status"], "initialized")
        self.assertEqual(created["note_count"], 1)
        self.assertEqual(created["chunk_count"], 2)
        self.assertEqual(created["categories"], {"architecture": 1, "testing": 1})
        self.assertNotIn("documents", created)
        self.assertEqual(original, self.note.read_bytes())
        self.assertEqual(self.store.memories(), [])  # Indexing does not confirm lessons.
        self.store.close()
        self.store = Store(self.db)
        with patch("context_lab.knowledge.os.walk", side_effect=AssertionError("must not scan again")):
            again = initiate(self.store, "course", "T-123", str(self.root / "missing"))
        self.assertEqual(again, dict(created, status="already_initialized"))
        self.assertEqual(len(self.store.sources()), 1)
        for doc in self.store.documents("course", "T-123"):
            self.assertIn(doc["claim"], self.store.source(doc["source_ids"][0])["body"])

    def test_ticket_isolation_applies_to_retrieval_evidence_and_lessons(self):
        initiate(self.store, "course", "T-123", str(self.notes))
        other = self.store.add_source({"project": "course", "ticket": "T-456",
                                       "title": "Connection pooling", "body": "OTHER_TICKET_SECRET PostgreSQL"})
        self.store.put_memories([{"id": "other", "project": "course", "ticket": "T-456",
            "kind": "constraint", "status": "confirmed", "title": other["title"], "claim": other["body"],
            "source_ids": [other["id"]]}])
        for strategy in STRATEGIES:
            task = {"project": "course", "ticket": "T-123", "query": "PostgreSQL connection pooling"}
            packet = compile_context(self.store, task, strategy, persist=False)
            self.assertTrue(packet["selected"])
            self.assertNotIn("OTHER_TICKET_SECRET", json.dumps(packet))
            self.assertNotIn("other", [t["id"] for t in packet["trace"]])
            self.assertLessEqual(estimated_tokens(packet["context"]), 1200)
            for scope in ({"ticket": "T-999"}, {"ticket": ""}, {"project": "another"}):
                self.assertEqual(compile_context(self.store, dict(task, **scope), strategy, persist=False)["selected"], [])
        with self.assertRaises(ValueError):
            call(self.store, "memory_source", {"cwd": self.temp.name, "source_id": other["id"], "project": "course", "ticket": "T-123"})
        sid = self.store.documents("course", "T-123")[0]["source_ids"][0]
        lesson = {"id": "lesson", "project": "course", "ticket": "T-123", "kind": "lesson",
                  "title": "Connection pooling", "claim": "Pool connections", "source_ids": [sid]}
        with self.assertRaises(ValueError):
            self.store.put_memories([dict(lesson, source_ids=[other["id"]])])
        with self.assertRaises(ValueError):
            self.store.put_memories([dict(lesson, depends_on=["other"])])
        call(self.store, "memory_propose", {"cwd": self.temp.name, "memories": [lesson]})
        self.assertEqual(self.store.memory("lesson")["status"], "candidate")

    def test_refresh_replaces_search_snapshot_and_failed_refresh_preserves_it(self):
        created = initiate(self.store, "course", "T-123", str(self.notes))
        old_sid = self.store.documents("course", "T-123")[0]["source_ids"][0]
        self.note.write_text("# Requirements\nReplacement requirement for offline caching.\n", encoding="utf-8")
        refreshed = initiate(self.store, "course", "T-123", refresh=True)
        self.assertEqual(refreshed["status"], "refreshed")
        self.assertEqual(created["initialized_at"], refreshed["initialized_at"])
        self.assertIn("PostgreSQL", self.store.source(old_sid)["body"])
        self.assertNotIn("PostgreSQL", json.dumps(self.store.documents("course", "T-123")))
        self.note.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            initiate(self.store, "course", "T-123", refresh=True)
        self.assertEqual(initiate(self.store, "course", "T-123"), dict(refreshed, status="already_initialized"))
        with self.assertRaises(ValueError):
            initiate(self.store, "course", "T-999", str(self.notes))
        self.assertIsNone(self.store.knowledge_base("course", "T-999"))

    def test_empty_setup_hidden_files_links_and_large_note_chunks(self):
        self.assertEqual(initiate(self.store, "empty", "T-1", empty=True)["status"], "initialized")
        self.assertEqual(initiate(self.store, "empty", "T-1")["status"], "already_initialized")
        (self.notes / ".private.md").write_text("DO_NOT_INDEX", encoding="utf-8")
        (self.notes / "outside.md").symlink_to(self.root / "outside.md")
        (self.root / "outside.md").write_text("DO_NOT_INDEX", encoding="utf-8")
        self.note.write_text("# Pooling\n" + "PostgreSQL connection pooling. " * 400, encoding="utf-8")
        result = initiate(self.store, "course", "T-123", str(self.notes))
        self.assertEqual(result["note_count"], 1)
        self.assertGreater(result["chunk_count"], 1)
        self.assertNotIn("DO_NOT_INDEX", json.dumps(self.store.export()))
        packet = compile_context(self.store, {"project": "course", "ticket": "T-123", "query": "PostgreSQL pooling"})
        self.assertTrue(packet["selected"])
        self.assertLessEqual(packet["estimated_tokens"], 1200)

    def test_cli_and_mcp_setup_across_processes(self):
        script = ROOT / "plugins/context-lab/skills/context-lab/scripts/context_lab.py"
        cmd = [sys.executable, str(script), "--db", self.db, "initiate", "--project", "course", "--ticket", "T-123"]
        result = subprocess.run(cmd + ["--path", str(self.notes), "--no-vault"], cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "initialized")
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_initiate",
                     "arguments": {"cwd": self.temp.name, "project": "course", "ticket": "T-123"}}}]
        stream = io.StringIO()
        serve_mcp(self.store, io.StringIO("\n".join(map(json.dumps, messages))), stream)
        result = json.loads(stream.getvalue().splitlines()[-1])["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["status"], "already_initialized")

    def test_concurrent_initiation_has_one_completed_setup(self):
        def setup(_):
            store = Store(self.db)
            try:
                return initiate(store, "course", "T-123", str(self.notes))["status"]
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=3) as executor:
            statuses = list(executor.map(setup, range(3)))
        self.assertEqual(statuses.count("initialized"), 1)
        self.assertEqual(statuses.count("already_initialized"), 2)
        self.assertEqual(len(self.store.sources()), 1)

    def test_legacy_database_migration_preserves_source(self):
        legacy = str(self.root / "legacy.sqlite3")
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL)")
            db.execute("INSERT INTO sources VALUES ('old','course','Old note','Old evidence','2026-01-01','hash')")
        store = Store(legacy)
        try:
            self.assertEqual(store.source("old")["body"], "Old evidence")
            self.assertEqual(store.source("old")["ticket"], "")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
