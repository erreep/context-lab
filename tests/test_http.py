import json
import select
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from context_lab.engine import DATA_ROOT, ROOT
from context_lab.store import Store


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "workspace").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT / "workspace")
        cls.db_path = str(Path(cls.temp.name) / "test.sqlite3")
        store = Store(cls.db_path)
        store.seed(DATA_ROOT / "memories.json")
        store.close()
        cls.process = subprocess.Popen([sys.executable, "-m", "context_lab", "--db", cls.db_path, "serve", "--port", "0"],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not select.select([cls.process.stdout], [], [], 10)[0]:
            cls.process.terminate()
            cls.process.wait(timeout=5)
            raise RuntimeError("HTTP server did not start")
        line = cls.process.stdout.readline().strip()
        if "http://127.0.0.1:" not in line:
            cls.process.terminate()
            cls.process.wait(timeout=5)
            raise RuntimeError("HTTP server startup failed: " + line)
        cls.base = line.split(" at ", 1)[1]
        cls.boot_pending = cls.process.stdout.readline().strip()

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stdout.close()
        cls.process.stderr.close()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read()
            return json.loads(body) if "json" in r.headers["Content-Type"] else body.decode()

    def test_page_and_example_picker(self):
        page = self.request("/")
        self.assertIn("Context Lab", page)
        self.assertNotIn('<option value="__global__"', page)
        self.assertIn('id="layer-stack"', page)
        self.assertIn("To review", page)
        self.assertIn("Nothing waiting.", page)
        self.assertIn('id="pending-badge"', page)
        self.assertIn("inbox-mode", page)
        self.assertIn("Lab tools", page)
        store = Store(self.db_path)
        try:
            waiting = sum(1 for m in store.memories() if m.get("status") == "candidate")
        finally:
            store.close()
        expected = "Nothing waiting to confirm" if waiting == 0 else f"{waiting} waiting to confirm"
        self.assertEqual(self.boot_pending, expected)
        self.assertIn("Lab rules", page)
        self.assertNotIn("Give the next decision", page)
        cases = self.request("/api/scenarios")["cases"]
        self.assertEqual(len(cases), 28)
        self.assertTrue(all("expected" not in c for c in cases))

    def test_three_arm_compare(self):
        result = self.request("/api/compare", {"task": {"project": "fieldnote", "query": "Change button color", "as_of": "2026-09-12"}})
        self.assertEqual(len(result["packets"]), 3)
        for p in result["packets"]:
            self.assertIn("run_id", p)
        self.assertEqual(result["packets"][-1]["selected"][0]["id"], "C-brand")

    def test_source_candidate_confirm_and_revision(self):
        source = self.request("/api/source", {"project": "test-http", "title": "Observed condition", "body": "Keep the switch off during inspection."})
        m = {"id": "http-rule", "project": "test-http", "kind": "constraint", "title": "Switch condition", "claim": source["body"], "source_ids": [source["id"]], "status": "candidate", "valid_from": "2026-01-01"}
        self.request("/api/memories", {"memories": [m]})
        self.request("/api/memories", {"memories": [dict(m, expected_version=1, status="confirmed")]})
        history = self.request("/api/revisions/http-rule")["revisions"]
        self.assertEqual([m["status"] for m in history], ["candidate", "confirmed"])
        self.assertEqual(self.request("/api/source/" + source["id"])["body"], source["body"])

    def test_bad_inputs_and_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.request("/api/compare", {"task": {"query": ""}})
        self.assertEqual(result.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.request("/api/source", {"project": "p", "title": "x", "body": "x"}, {"Origin": "https://unrelated.example"})
        self.assertEqual(result.exception.code, 403)

    def test_mcp_subprocess(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"task": {"project": "fieldnote", "query": "Change button color", "as_of": "2026-09-12"}}}}]
        result = subprocess.run([sys.executable, "-m", "context_lab", "--db", self.db_path, "mcp"],
                                cwd=ROOT, input="\n".join(map(json.dumps, messages)) + "\n", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertFalse(response[-1]["result"]["isError"])
        self.assertIn("C-brand", response[-1]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
