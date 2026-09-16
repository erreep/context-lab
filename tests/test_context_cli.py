"""Subprocess regression: context CLI flags reach compile execution."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from context_lab.engine import ROOT
from context_lab.store import Store


class _RecordingHandler(BaseHTTPRequestHandler):
    paths = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.paths.append(self.path)
        if self.path.endswith("/embeddings"):
            inputs = body.get("input") or []
            payload = {
                "data": [
                    {"index": i, "embedding": [1.0, float(i + 1) / 10.0]}
                    for i in range(len(inputs))
                ]
            }
        else:
            payload = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "actions": ["sync"],
                            "needs": ["offline_mode"],
                        }),
                    },
                }],
            }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


class ContextCliFlagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = str(self.root / "memory.sqlite3")
        subprocess.run(
            [sys.executable, "-m", "context_lab", "--db", self.db, "demo"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.task = self.root / "task.json"
        self.task.write_text(json.dumps({
            "query": "Add background uploads that retry after reconnecting.",
            "project": "fieldnote",
            "as_of": "2026-09-12",
            "state": {"idempotency_verified": False, "http_method": "POST", "single_device": True},
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _run_context(self, *flags, env=None):
        merged = dict(os.environ)
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, "-m", "context_lab", "--db", self.db, "context", "--task", str(self.task), *flags],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            env=merged,
        )

    def _saved_run(self, run_id):
        store = Store(self.db)
        try:
            return store.run(run_id)
        finally:
            store.close()

    def test_strategy_flag_reaches_compile(self):
        result = self._run_context("--strategy", "lessons")
        self.assertEqual(result.returncode, 0, result.stderr)
        view = json.loads(result.stdout)
        run = self._saved_run(view["run_id"])
        self.assertEqual(run["strategy"], "lessons")

    def test_model_flags_reach_execution(self):
        server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        _RecordingHandler.paths = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        env = {
            "CONTEXT_LAB_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "CONTEXT_LAB_MODEL": "test-chat",
            "CONTEXT_LAB_EMBEDDING_MODEL": "test-embed",
            "CONTEXT_LAB_LOCAL_ONLY": "1",
        }
        try:
            result = self._run_context(
                "--strategy", "retrieval",
                "--model-planner",
                "--embeddings",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            view = json.loads(result.stdout)
            run = self._saved_run(view["run_id"])
            self.assertEqual(run["strategy"], "retrieval")
            self.assertEqual(run["task"]["planning"]["method"], "model_proposed")
            self.assertIn("embeddings", run["backend"])
            self.assertTrue(any(p.endswith("/chat/completions") for p in _RecordingHandler.paths))
            self.assertTrue(any(p.endswith("/embeddings") for p in _RecordingHandler.paths))
        finally:
            server.shutdown()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
