"""Offline bridge checks; set CONTEXT_LAB_MEM0_LIVE=1 to exercise local Ollama too."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from context_lab.engine import ROOT, STRATEGIES, compile_context
from context_lab.mem0_bridge import extract, identity, source_metadata
from context_lab.mcp import call
from context_lab.service import dispatch
from context_lab.store import Store


class Mem0Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.source = self.store.add_source({"project": "Synthetic bridge test", "ticket": "TEST-1",
                                            "title": "Synthetic retry observation",
                                            "body": "The upload retry limit for this ticket is seven attempts."})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def row(self, source=None):
        source = source or self.source
        return {"id": str(uuid.uuid4()), "memory": source["body"], **identity(source),
                "metadata": source_metadata(source)}

    def worker(self, response):
        return patch("context_lab.mem0_bridge.subprocess.run",
                     return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response)))

    def request(self, **changes):
        return {"source_id": self.source["id"], "project": self.source["project"],
                "ticket": self.source["ticket"], **changes}

    def test_candidates_review_isolation_and_retry_preserves_review(self):
        row = self.row()
        with self.worker({"results": [row]}):
            result = call(self.store, "memory_extract", self.request())
            candidate = result["memories"][0]
            self.assertEqual(candidate["status"], "candidate")
            task = {"project": self.source["project"], "ticket": "TEST-1", "query": "upload retry limit"}
            for arm in STRATEGIES:
                self.assertFalse(compile_context(self.store, task, arm, persist=False)["selected"])
            self.store.put_memories([dict(candidate, status="confirmed", expected_version=1)])
            for arm in STRATEGIES:
                self.assertTrue(compile_context(self.store, task, arm, persist=False)["selected"])
                for scope in ({"ticket": "TEST-2"}, {"ticket": ""}, {"project": "another"}):
                    self.assertFalse(compile_context(self.store, dict(task, **scope), arm, persist=False)["selected"])
            again = dispatch(self.store, "mem0-extract", self.request())
            self.assertEqual(again["created"], 0)
            self.assertEqual(again["memories"][0]["status"], "confirmed")
            self.assertEqual(len(self.store.revisions(candidate["id"])), 2)
            self.assertEqual(self.store.source(self.source["id"]), self.source)

    def test_reject_scope_and_size_before_starting_worker(self):
        with self.worker({"results": []}) as worker:
            for scope in ({"ticket": "TEST-2"}, {"ticket": ""}, {"project": "another"}):
                with self.assertRaisesRegex(ValueError, "project/ticket"):
                    extract(self.store, **self.request(**scope))
            long = self.store.add_source(dict(self.source, id="long", body="é" * 2001))
            with self.assertRaisesRegex(ValueError, "4000"):
                extract(self.store, **self.request(source_id=long["id"]))
            worker.assert_not_called()

    def test_invalid_output_is_atomic(self):
        row = self.row()
        bad_rows = [dict(row, run_id="wrong"), dict(row, user_id="wrong"),
                    dict(row, metadata={}), dict(row, memory=""), dict(row, id=None),
                    dict(row, metadata=dict(row["metadata"], ticket="TEST-2"))]
        for bad in bad_rows:
            with self.worker({"results": [row, bad]}), self.assertRaises(ValueError):
                extract(self.store, **self.request())
            self.assertFalse(self.store.memories())

    def test_worker_errors_leave_evidence_and_no_candidates(self):
        with self.worker({"error": "Ollama unavailable"}), self.assertRaisesRegex(ValueError, "Ollama"):
            extract(self.store, **self.request())
        with patch("context_lab.mem0_bridge.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 180)):
            with self.assertRaisesRegex(ValueError, "timed out"):
                extract(self.store, **self.request())
        self.assertFalse(self.store.memories())
        self.assertEqual(self.store.source(self.source["id"]), self.source)

    def test_identity_is_stable_and_unambiguous(self):
        self.assertEqual(identity(self.source), identity(dict(self.source, project="  Synthetic bridge test  ")))
        self.assertNotEqual(identity(self.source)["user_id"], identity(dict(self.source, ticket="TEST-2"))["user_id"])
        self.assertNotEqual(identity(self.source)["run_id"], identity(dict(self.source, id="different"))["run_id"])
        self.assertNotEqual(identity(dict(self.source, project="a:b", ticket="c"))["user_id"],
                            identity(dict(self.source, project="a", ticket="b:c"))["user_id"])

    @unittest.skipUnless(os.environ.get("CONTEXT_LAB_MEM0_LIVE") == "1", "optional local Ollama integration")
    def test_live_local_extraction_and_cross_ticket_separation(self):
        # All SQLite, vector and history files live in the disposable test directory.
        command = [sys.executable, "-m", "context_lab", "--db", self.store.path, "mem0-extract",
                   "--source", self.source["id"], "--project", self.source["project"], "--ticket", "TEST-1"]
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=200)
        self.assertEqual(run.returncode, 0, run.stderr)
        first = json.loads(run.stdout)
        self.assertGreater(first["created"], 0, first)
        second_source = self.store.add_source(dict(self.source, id="synthetic-second", ticket="TEST-2",
                                                   body="The upload retry limit for this ticket is two attempts."))
        second = dispatch(self.store, "mem0-extract", self.request(source_id=second_source["id"], ticket="TEST-2"))
        self.assertGreater(second["created"], 0, second)
        for result, source in ((first, self.source), (second, second_source)):
            for candidate in result["memories"]:
                self.assertEqual(candidate["source_ids"], [source["id"]])
                self.assertEqual(candidate["ticket"], source["ticket"])
                self.assertEqual(candidate["status"], "candidate")
                self.store.put_memories([dict(candidate, status="confirmed", expected_version=1)])
            task = {"project": source["project"], "ticket": source["ticket"], "query": "upload retry limit"}
            packet = compile_context(self.store, task, persist=False)
            self.assertTrue(packet["selected"])
            self.assertTrue(all(m["ticket"] == source["ticket"] for m in packet["selected"]))
        repeated = call(self.store, "memory_extract", self.request())
        self.assertEqual(repeated["created"], 0)
        self.assertTrue(all(m["status"] == "confirmed" for m in repeated["memories"]))
        print("Live local Mem0:", json.dumps({"TEST-1": [m["claim"] for m in first["memories"]],
                                              "TEST-2": [m["claim"] for m in second["memories"]]}))


if __name__ == "__main__":
    unittest.main()
