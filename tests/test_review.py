"""Review handoff: live URL only after matching health; empty inbox is truthful."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from context_lab.hooks import install
from context_lab.review import (
    ReviewReady,
    ReviewRoute,
    ReviewUnavailable,
    database_id,
    health_payload,
    inbox_state,
    open_review,
    print_install_handoff,
)
from context_lab.store import Store


def _db() -> Path:
    root = Path(tempfile.mkdtemp())
    path = root / "memory.sqlite3"
    Store(str(path)).close()
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ReviewUnitTests(unittest.TestCase):
    def test_health_payload_is_digest_not_path(self):
        path = _db()
        payload = health_payload(path)
        self.assertEqual(payload["service"], "context-lab")
        self.assertEqual(payload["database"], database_id(path))
        self.assertNotIn(str(path), json.dumps(payload))

    def test_inbox_state_distinguishes_fresh_and_caught_up(self):
        path = _db()
        store = Store(str(path))
        try:
            fresh = inbox_state(store)
            self.assertEqual(fresh["stage"], "fresh")
            self.assertIn("set up", fresh["headline"].lower())
            source = store.add_source({
                "project": "app",
                "ticket": "",
                "title": "src",
                "body": "Evidence for a settled lesson.",
            })
            store.put_memories([{
                "id": "m1",
                "project": "app",
                "ticket": "",
                "kind": "lesson",
                "status": "confirmed",
                "title": "t",
                "claim": "c",
                "source_ids": [source["id"]],
            }])
            caught = inbox_state(store, "app", "")
            self.assertEqual(caught["stage"], "caught_up")
            self.assertIn("zero", caught["headline"].lower())
        finally:
            store.close()

    def test_unavailable_has_no_url(self):
        outcome = ReviewUnavailable(reason="x", retry_command=("context-lab", "review"))
        self.assertFalse(hasattr(outcome, "url"))


class ReviewLiveTests(unittest.TestCase):
    def test_open_review_on_free_port_is_ready_and_idempotent(self):
        path = _db()
        port = _free_port()
        with mock.patch("context_lab.review.webbrowser.open", return_value=True):
            first = open_review(path, open_browser=True, preferred_port=port)
            self.assertIsInstance(first, ReviewReady, msg=repr(first))
            self.assertTrue(first.url.startswith(f"http://127.0.0.1:{port}"))
            body = json.loads(
                urllib.request.urlopen(first.url.rstrip("/") + "/api/health", timeout=2).read()
            )
            self.assertEqual(body, health_payload(path))
            second = open_review(path, open_browser=False, preferred_port=port)
            self.assertIsInstance(second, ReviewReady)
            self.assertEqual(second.url.split("?")[0], first.url.split("?")[0])
            self.assertEqual(second.origin.value, "reused")

    def test_install_handoff_non_tty_does_not_claim_live_url(self):
        path = _db()
        buf = io.StringIO()
        outcome = print_install_handoff(path, ReviewRoute("app", "T-1"), out=buf)
        text = buf.getvalue()
        self.assertIsInstance(outcome, ReviewUnavailable)
        self.assertNotIn("live at http://", text)
        self.assertIn("context-lab", text)
        self.assertRegex(text, r"\breview\b")
        self.assertIn("--project app", text)


class InstallHandoffTests(unittest.TestCase):
    def test_install_stdout_mentions_review_not_bare_serve_url(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "lab@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Lab"], cwd=repo, check=True, capture_output=True)
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        out, err = io.StringIO(), io.StringIO()
        os.environ["CONTEXT_LAB_REVIEW"] = "0"
        with redirect_stdout(out), redirect_stderr(err):
            code = install("cursor", project="app", ticket="T-1", git=False, force=True, cwd=str(repo))
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("Review UI", text)
        self.assertNotIn("Confirm waiting candidates at http://127.0.0.1:8765", text)


if __name__ == "__main__":
    unittest.main()
