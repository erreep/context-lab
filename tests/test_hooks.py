"""Harness hooks: set-scope, inject, session-start."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import initiate
from context_lab.hooks import print_config, set_scope
from context_lab.store import Store


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _run_hook(cwd, command, payload, env=None, argv=()):
    merged = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "context_lab", "hook", command, *argv],
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

    def test_session_start_injects_standing_context_stub(self):
        result = _run_hook(self.repo, "session-start", {"session_id": "s1", "cwd": str(self.repo)})
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("standing context", out["additionalContext"])
        self.assertIn("run_id", out["additionalContext"])
        self.assertNotIn("Context Lab hard gates", out["additionalContext"])
        self.assertNotIn("Context Lab contract", out["additionalContext"].split("\n\n", 1)[0])

    def test_gate_adapter_reads_command_and_cwd_from_payload(self):
        # Run from a directory that is not a repo: only the payload can supply cwd and command.
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        passthrough = _run_hook(elsewhere, "gate-git", {
            "cwd": str(self.repo), "tool_input": {"command": "ls -la"},
        }, argv=["commit", "--adapter", "claude"])
        self.assertEqual(passthrough.returncode, 0, passthrough.stderr)
        self.assertEqual(passthrough.stdout.strip(), "")
        denied = _run_hook(elsewhere, "gate-git", {
            "cwd": str(self.repo), "tool_input": {"command": "git -C . commit -m x"},
        }, argv=["commit", "--adapter", "claude"])
        self.assertEqual(denied.returncode, 0, denied.stderr)
        out = json.loads(denied.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("missing lease", out["permissionDecisionReason"])
        cursor = _run_hook(elsewhere, "gate-git", {
            "workspace_roots": [str(self.repo)], "command": "git commit -m x",
        }, argv=["commit", "--adapter", "cursor"])
        self.assertEqual(json.loads(cursor.stdout)["permission"], "deny")

    def test_missing_scope_silent_stdout(self):
        other = self.root / "other"
        other.mkdir()
        _git(other, "init")
        result = _run_hook(other, "inject", {
            "session_id": "s2", "cwd": str(other), "prompt": "hello",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("scope bind", result.stderr)


class HookOptInTests(unittest.TestCase):
    def test_repository_ships_no_active_hooks_but_keeps_opt_in_generator(self):
        root = Path(__file__).resolve().parents[1]
        paths = (".claude/settings.json", ".codex/hooks.json", ".cursor/hooks.json")
        ignored = (root / ".gitignore").read_text().splitlines()
        self.assertIn(".claude/settings.json", ignored)
        self.assertIn(".codex/hooks.json", ignored)
        self.assertIn(".cursor/", ignored)
        for relative in paths:
            self.assertFalse((root / relative).exists(), relative)
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(print_config("codex"), 0)
        self.assertIn("UserPromptSubmit", json.loads(stdout.getvalue())["hooks"])
        self.assertIn(".codex/hooks.json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
