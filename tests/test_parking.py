import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from context_lab.engine import compile_context
from context_lab.mcp import call
from context_lab.parking import ExistingTicket, NewTicket, ParkingLot, ParkingError
from context_lab.review import inbox_state
from context_lab.schemas import AgentError
from context_lab.scope import BranchScopes, MemoryScope
from context_lab.store import GLOBAL_PROJECT, Store
from tests.git_support import run_git


SECRET = "PARK_SECRET_NEVER_RECALL"
PROJECT = "course"
TICKET = "T-1"


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "memory.sqlite3"))
        self.lot = ParkingLot(self.store)
        self._cwd = os.getcwd()
        os.chdir(self.temp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self.store.close()
        self.temp.cleanup()

    def _park(self, **kwargs):
        defaults = {
            "project": PROJECT,
            "title": "Later fix",
            "body": SECRET,
            "captured_by": "test",
            "captured_while_ticket": TICKET,
        }
        defaults.update(kwargs)
        return self.lot.capture(**defaults)

    def test_parked_phrase_absent_from_recall_and_inbox(self):
        before = inbox_state(self.store, PROJECT, TICKET)
        self._park()
        after = inbox_state(self.store, PROJECT, TICKET)
        self.assertEqual(before["waiting"], after["waiting"])

        queries = [
            {"project": PROJECT, "ticket": TICKET, "query": "pool upload retry"},
            {"project": PROJECT, "ticket": "T-2", "query": "pool upload retry"},
            {"project": PROJECT, "query": "pool upload retry"},
            {"project": GLOBAL_PROJECT, "query": "lab standing rules"},
        ]
        for task in queries:
            packet = compile_context(self.store, task, persist=False)
            blob = json.dumps(packet)
            self.assertNotIn(SECRET, blob, msg=json.dumps(task))

    def test_start_creates_candidate_and_replays_command(self):
        item = call(self.store, "memory_park", {
            "cwd": self.temp.name,
            "project": PROJECT,
            "title": "Badge drift",
            "body": "Header counts only review rows.",
        })["item"]
        command_id = "cmd-" + uuid.uuid4().hex[:12]
        first = self.lot.start(item["id"], NewTicket(), command_id=command_id)
        candidate = self.store.memory(first["candidate_id"])
        self.assertEqual(candidate["status"], "candidate")
        self.assertTrue(candidate["ticket"])
        self.assertNotEqual(candidate["ticket"], TICKET)
        self.assertIn("Badge drift", candidate["title"])
        self.assertIn("Badge drift", candidate["claim"])
        source = self.store.source(first["source_id"])
        self.assertIn("Badge drift", source["body"])

        second = self.lot.start(item["id"], NewTicket(), command_id=command_id)
        self.assertEqual(first, second)

        with self.assertRaises(ParkingError):
            self.lot.start(item["id"], NewTicket(), command_id="cmd-other")

    def test_dismiss_removes_from_default_list(self):
        item = self._park(title="Dismiss me", body="gone soon")
        before = inbox_state(self.store, PROJECT, "")
        self.lot.dismiss(item["id"], command_id="cmd-dismiss-1")
        self.assertEqual(self.lot.list(project=PROJECT), [])
        after = inbox_state(self.store, PROJECT, "")
        self.assertEqual(before["waiting"], after["waiting"])

    def test_capture_key_is_idempotent(self):
        first = self._park(capture_key="retry-key-1")
        second = self._park(capture_key="retry-key-1")
        self.assertEqual(first["id"], second["id"])

    def test_start_rejects_empty_ticket(self):
        item = self._park(title="Needs a ticket", body="not baseline")
        with self.assertRaises(ParkingError):
            self.lot.start(item["id"], ExistingTicket(""), command_id="cmd-empty")
        self.assertEqual(self.lot.get(item["id"])["state"], "parked")

    def test_export_includes_parked_rows(self):
        item = self._park(title="Keep me", body="export body")
        payload = self.store.export()
        self.assertEqual(payload["parked_items"][0]["id"], item["id"])
        self.assertEqual(payload["parked_items"][0]["body"], "export body")

    def test_park_only_project_appears_in_scopes_and_later(self):
        """Workbench catalog is scopes-backed; park-only projects must still be selectable."""
        from context_lab.service import info, scopes

        self._park(project="solo", title="Only park", body="no sources yet")
        self.assertIn("solo", scopes(self.store)["by_project"])
        self.assertIn("solo", info(self.store)["projects"])
        later = info(self.store, "solo", "")["later"]
        self.assertEqual(later["count"], 1)
        self.assertEqual(later["items"][0]["title"], "Only park")

    def test_global_project_rejected(self):
        with self.assertRaises(ParkingError):
            self.lot.capture(
                project=GLOBAL_PROJECT,
                title="nope",
                body="nope",
                captured_by="test",
            )
        with self.assertRaises(AgentError):
            call(self.store, "memory_park", {
                "cwd": self.temp.name,
                "project": GLOBAL_PROJECT,
                "title": "nope",
                "body": "nope",
            })

    def test_park_records_bound_ticket_and_rejects_other_project(self):
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        home = Path(self.temp.name)
        run_git(repo, "init", home=home)
        run_git(repo, "config", "user.email", "lab@example.com", home=home)
        run_git(repo, "config", "user.name", "Lab", home=home)
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        run_git(repo, "add", "a.txt", home=home)
        run_git(repo, "commit", "-m", "init", home=home)
        BranchScopes.bind_current(
            str(repo), MemoryScope(PROJECT, TICKET),
            database=self.store.path,
        )
        os.chdir(repo)
        item = call(self.store, "memory_park", {
            "cwd": str(repo),
            "project": PROJECT,
            "title": "Bound capture",
            "body": "while on T-1",
        })["item"]
        shown = self.lot.get(item["id"])
        self.assertEqual(shown["captured_while_ticket"], TICKET)
        with self.assertRaises(AgentError) as ctx:
            call(self.store, "memory_park", {
                "cwd": str(repo),
                "project": "other-app",
                "title": "Wrong project",
                "body": "should fail",
            })
        self.assertEqual(ctx.exception.code, "scope_mismatch")

