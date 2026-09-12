"""Harness hooks: set-scope, inject, session-start."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import initiate
from context_lab.hooks import set_scope
from context_lab.store import Store


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _run_hook(cwd, command, payload, env=None):
    merged = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "context_lab", "hook", command],
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
    )


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "lab@example.com")
        _git(self.repo, "config", "user.name", "Lab")
        self.db = str(self.root / "memory.sqlite3")
        self.store = Store(self.db)
        with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
            initiate(self.store, "app", "T-1", knowledge={"mode": "empty", "vault": "none"})
        src_a = self.store.add_source({
            "project": "app", "ticket": "T-1", "title": "alpha-src",
            "body": "AlphaWidget uses cobalt pigment exclusively.",
        })
        src_b = self.store.add_source({
            "project": "app", "ticket": "T-1", "title": "beta-src",
            "body": "BetaWidget must never share AlphaWidget pigment stock.",
        })
        self.store.put_memories([
            {
                "id": "M-alpha", "project": "app", "ticket": "T-1", "kind": "constraint",
                "status": "confirmed", "title": "Alpha pigment",
                "claim": "AlphaWidget uses cobalt pigment exclusively.",
                "source_ids": [src_a["id"]],
            },
            {
                "id": "M-beta", "project": "app", "ticket": "T-1", "kind": "constraint",
                "status": "confirmed", "title": "Beta stock split",
                "claim": "BetaWidget must never share AlphaWidget pigment stock.",
                "source_ids": [src_b["id"]],
            },
        ])
        set_scope("app", "T-1", db=self.db, cwd=str(self.repo))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_inject_compactview_and_distinct_runs(self):
        first = _run_hook(self.repo, "inject", {
            "session_id": "s1", "cwd": str(self.repo),
            "prompt": "What pigment does AlphaWidget use?",
        })
        self.assertEqual(first.returncode, 0, first.stderr)
        out1 = json.loads(first.stdout)
        ctx1 = json.loads(out1["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(out1["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("run_id", ctx1)
        self.assertIn("context", ctx1)
        self.assertIn("picks", ctx1)
        self.assertIn("wire_estimated_tokens", ctx1)

        second = _run_hook(self.repo, "inject", {
            "session_id": "s1", "cwd": str(self.repo),
            "prompt": "May BetaWidget share AlphaWidget pigment stock?",
        })
        self.assertEqual(second.returncode, 0, second.stderr)
        ctx2 = json.loads(json.loads(second.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertNotEqual(ctx1["run_id"], ctx2["run_id"])
        self.assertNotEqual(ctx1.get("picks"), ctx2.get("picks"))
        blob1 = json.dumps(ctx1)
        blob2 = json.dumps(ctx2)
        self.assertIn("Alpha", blob1)
        self.assertIn("Beta", blob2)

    def test_missing_scope_silent_stdout(self):
        other = self.root / "other"
        other.mkdir()
        _git(other, "init")
        result = _run_hook(other, "inject", {
            "session_id": "s2", "cwd": str(other), "prompt": "hello",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("set-scope", result.stderr)


if __name__ == "__main__":
    unittest.main()
