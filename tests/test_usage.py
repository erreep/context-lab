"""Count delivered memory payloads, preserve scope, and never invoke a model."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab import agent_api, hooks, usage
from tests.git_support import run_git
from context_lab.mcp import serve_mcp
from context_lab.schemas import GATE_TEXT, wire_dumps
from context_lab.scope import BranchScopes, MemoryScope
from context_lab.store import Store


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "memory.sqlite3")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.enterContext(patch.dict(os.environ, {"CONTEXT_LAB_BASE_URL": ""}))
        self.enterContext(patch("context_lab.provider.ModelEndpoint.request", side_effect=AssertionError("Unexpected model call")))

    def exchange(self, calls):
        messages = [{"jsonrpc": "2.0", "id": 0, "method": "initialize"}]
        for i, (name, args) in enumerate(calls, 1):
            if isinstance(args, dict) and name not in {"memory_catalog", "memory_allocate_ticket"}:
                args = {"cwd": self.temp.name, **args}
            messages.append({
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            })
        out = io.StringIO()
        serve_mcp(self.store, io.StringIO("\n".join(map(json.dumps, messages))), out)
        return [json.loads(line)["result"] for line in out.getvalue().splitlines()]

    def test_mcp_unicode_errors_and_payload_only(self):
        args = {"project": "app", "ticket": "768", "title": "café", "body": "日本語 🧠"}
        results = self.exchange([
            ("memory_observe", args),
            ("memory_observe", dict(args, title="")),
            ("memory_context", {"task": {"project": "app", "ticket": "768", "query": "recall"}}),
            ("memory_observe", []),
        ])
        rows = list(self.store.db.execute("SELECT * FROM usage_events WHERE channel='mcp' ORDER BY id"))
        self.assertEqual(len(rows), 4)
        sent_args = {"cwd": self.temp.name, **args}
        expected_request = (
            len(wire_dumps({"name": "memory_observe", "arguments": sent_args}).encode()) + 3
        ) // 4
        self.assertEqual(rows[0]["request_estimated_tokens"], expected_request)
        for row, response in zip(rows, results[1:]):
            text = response["content"][0]["text"]
            self.assertEqual(row["response_estimated_tokens"], (len(text.encode()) + 3) // 4)
            self.assertEqual(set(response), {"content", "isError"})
        self.assertTrue(results[2]["isError"])
        self.assertTrue(results[4]["isError"])
        self.assertIsNone(rows[-1]["project"])
        recall = json.loads(results[3]["content"][0]["text"])
        self.assertEqual(rows[2]["response_estimated_tokens"], recall["wire_estimated_tokens"])
        self.assertNotIn("日本語", str([dict(row) for row in rows]))
        self.assertEqual(usage.report(self.store, "app", "768")["events"], 3)

    def test_setup_and_mixed_batches_stay_unassigned(self):
        drafts = []
        for project, ticket in [("app", "768"), ("other", "768")]:
            source = self.store.add_source({"project": project, "ticket": ticket, "title": "t", "body": "b"})
            drafts.append({"project": project, "ticket": ticket, "kind": "lesson", "title": "t",
                           "claim": "b", "source_ids": [source["id"]]})
        results = self.exchange([("memory_propose", {"memories": drafts}), ("memory_catalog", {})])
        self.assertFalse(results[1]["isError"])
        self.assertEqual(usage.report(self.store, "app")["events"], 0)
        self.assertEqual(usage.report(self.store)["events"], 3)
        self.assertEqual(usage.report(self.store)["unassigned_events_in_database"], 3)
        out = io.StringIO()
        serve_mcp(self.store, io.StringIO('\n'.join(map(json.dumps, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]))), out)
        tools = json.loads(out.getvalue().splitlines()[1])["result"]["tools"]
        row = self.store.db.execute("SELECT * FROM usage_events WHERE operation='tools/list'").fetchone()
        self.assertIsNone(row["project"])
        self.assertEqual(row["response_estimated_tokens"], (len(wire_dumps(tools).encode()) + 3) // 4)

    def test_id_only_calls_use_original_scope(self):
        source = self.store.add_source({"project": "app", "ticket": "768", "title": "t", "body": "b"})
        self.store.put_memories([{"id": "m", "project": "app", "ticket": "768", "kind": "lesson",
                                 "title": "t", "claim": "b", "source_ids": [source["id"]], "status": "confirmed"}])
        run = agent_api.context(self.store, {"project": "app", "ticket": "768", "query": "recall"})
        self.assertEqual(usage.report(self.store)["events"], 0)  # local recall is not MCP traffic
        results = self.exchange([
            ("memory_inspect_run", {"run_id": run["run_id"]}),
            ("memory_feedback", {"run_id": run["run_id"], "memory_id": "m", "observation": "helpful"}),
            ("memory_promote", {"memory_id": "m"}),
        ])
        self.assertTrue(all(not r["isError"] for r in results[1:]))
        self.assertEqual(usage.report(self.store, "app", "768")["events"], 3)
        self.assertEqual(usage.report(self.store, "app", "")["events"], 0)

    def test_hook_injection_counts_full_text_without_prompt(self):
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        run_git(repo, "init", home=Path(self.temp.name))
        BranchScopes.bind_current(
            repo,
            MemoryScope("app", "768"),
            database=self.path,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hooks.inject({"cwd": str(repo), "prompt": "Recall café 日本語"})
        identity_text = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(json.loads(identity_text), {"place": "app/768"})
        self.assertIsNone(self.store.db.execute(
            "SELECT * FROM usage_events WHERE operation='UserPromptSubmit'",
        ).fetchone())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hooks.session_start({"cwd": str(repo)})
        text = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
        row = self.store.db.execute(
            "SELECT * FROM usage_events WHERE operation='SessionStart'",
        ).fetchone()
        self.assertEqual(row["request_estimated_tokens"], 0)
        self.assertEqual(row["response_estimated_tokens"], (len(text.encode()) + 3) // 4)
        self.assertEqual(row["ticket"], "768")
        self.assertTrue(text.startswith("Context Lab: standing context"))
        self.assertNotIn(GATE_TEXT.strip().splitlines()[0], text.splitlines()[:1])
        self.assertEqual(usage.report(self.store, "app", "768")["events"], 1)

    def test_journal_is_metered_once_per_call_even_on_retry(self):
        notes = Path(self.temp.name) / "notes"
        notes.mkdir()
        (notes / "seed.md").write_text("Initial ticket notes.\n", encoding="utf-8")
        with patch("context_lab.knowledge.discover_obsidian_vaults", return_value=[]):
            agent_api.initiate(self.store, "app", "768",
                               knowledge={"mode": "import", "path": str(notes), "vault": "none"})
        args = {"project": "app", "ticket": "768", "kind": "progress", "title": "Meter check",
                "body": "Journal traffic is included in local estimates."}
        results = self.exchange([("memory_journal", args), ("memory_journal", args)])
        self.assertTrue(all(not r["isError"] for r in results[1:]))
        self.assertEqual(len(list((notes / "journal").glob("*.md"))), 1)
        data = usage.report(self.store, "app", "768")
        self.assertEqual(data["events"], 2)  # one durable file, two delivered responses
        self.assertEqual(data["breakdown"][0]["operation"], "memory_journal")
        self.assertGreater(data["request_estimated_tokens"], 0)
        self.assertGreater(data["response_estimated_tokens"], 0)

    def test_report_cli_scope_filters_and_persistence(self):
        for scope in [("app", "768"), ("app", "769"), ("other", "768"), ("app", ""), None]:
            usage.record(self.store, "mcp", "memory_observe", scope, request="abcde", response="日本語")
        for flags, count in [([], 5), (["--project", "app"], 3),
                             (["--project", "app", "--ticket", "768"], 1),
                             (["--project", "app", "--ticket", ""], 1),
                             (["--project", "missing"], 0)]:
            result = subprocess.run([sys.executable, "-m", "context_lab", "--db", self.path, "usage", "--json", *flags],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["events"], count)
            self.assertEqual(data["total_estimated_tokens"], count * 5)
            self.assertIn("not actual model tokens", data["estimator"])
        with self.assertRaises(ValueError):
            usage.report(self.store, ticket="768")
        self.assertIn("No recorded traffic", usage.format_report(usage.report(self.store, "missing")))
        self.assertEqual(usage.report(self.store)["events"], 5)  # reports do not meter themselves

    def test_failed_meter_does_not_fail_completed_write(self):
        self.store.db.execute("DROP TABLE usage_events")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            results = self.exchange([("memory_observe", {"project": "app", "title": "t", "body": "saved"})])
        self.assertFalse(results[1]["isError"])
        self.assertIn("report may be incomplete", stderr.getvalue())
        self.assertEqual(self.store.sources()[0]["body"], "saved")
        # Opening an older database creates the meter table without losing its sources.
        with contextlib.closing(Store(self.path)) as reopened:
            self.assertEqual(usage.report(reopened)["events"], 0)
            self.assertEqual(reopened.sources()[0]["body"], "saved")

    def test_invalid_unicode_does_not_break_tool_result(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            results = self.exchange([("memory_catalog", {"unused": "\ud800"})])
        self.assertFalse(results[1]["isError"])
        self.assertIn("report may be incomplete", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
