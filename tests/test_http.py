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
        self.assertIn("Memories", page)
        self.assertNotIn("To review", page)
        self.assertIn("Nothing to confirm.", page)
        self.assertIn('id="pending-badge"', page)
        self.assertIn('id="later-badge"', page)
        self.assertIn("data-later-ticket", page)
        self.assertIn("data-open-later", page)
        self.assertIn("Parked while on", page)
        self.assertIn("saved for later — open Later", page)
        self.assertIn("Remove this from Later?", page)
        self.assertNotIn("prompt(", page)
        self.assertIn('id="create-dialog"', page)
        self.assertIn("function openCreateDialog(", page)
        self.assertNotIn("create-allocate", page)
        self.assertNotIn("api('allocate-ticket'", page)
        self.assertIn("$('new-ticket').disabled=!current", page)
        self.assertIn("parseProject(name)", page)
        self.assertIn("parseTicket(name)", page)
        self.assertIn("inbox-mode", page)
        self.assertIn("Agent preview", page)
        self.assertNotIn("Lab tools", page)
        self.assertIn(">Lab rules<", page)
        self.assertIn('placeholder="Filter"', page)
        self.assertNotIn("Filter to confirm", page)
        self.assertNotIn("One by one", page)
        self.assertNotIn("Batch OK", page)
        self.assertIn("evidence-drop", page)
        self.assertIn('id="global-evidence"', page)
        self.assertIn("renderGlobalEvidence(memory.source_ids", page)
        store = Store(self.db_path)
        try:
            waiting = sum(1 for m in store.memories() if m.get("status") == "candidate")
        finally:
            store.close()
        expected = "Nothing waiting to confirm" if waiting == 0 else f"{waiting} waiting to confirm"
        self.assertEqual(self.boot_pending, expected)
        self.assertIn('aria-label="Workspace"', page)
        self.assertIn('aria-label="Work item"', page)
        self.assertIn("This workspace", page)
        self.assertIn("New workspace", page)
        self.assertIn("New work item", page)
        self.assertIn("?'Workspace':'Work item'", page)
        self.assertNotIn("Project defaults", page)
        self.assertNotIn("Give the next decision", page)
        # Named <input name="id"> shadows HTMLFormElement.id; submit dispatch must use getAttribute.
        self.assertIn("const formId=node=>node.getAttribute('id');", page)
        self.assertIn("formId(event.target)==='global-memory-form'", page)
        self.assertNotIn("event.target.id==='global-memory-form'", page)
        self.assertIn("review_ack", page)
        self.assertIn("canConfirmReview", page)
        self.assertIn("review-card--must", page)
        self.assertNotIn("One by one", page)
        self.assertNotIn("Batch OK", page)
        global_form_start = page.index('id="global-memory-form"')
        global_form_end = page.index("</form>", global_form_start)
        global_form = page[global_form_start:global_form_end]
        self.assertNotIn("<option>confirmed</option>", global_form)
        self.assertNotIn('name="status"', global_form)
        self.assertIn("function similarWaitingRows(", page)
        self.assertIn("similar-waiting", page)
        self.assertIn("data-memory-id", page)
        self.assertNotIn("similar_waiting", page)
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

    def test_info_memories_include_review_tier(self):
        source = self.request("/api/source", {
            "project": "fieldnote", "ticket": "T-tier",
            "title": "Tier harness observation", "body": "Constraint body for tier test.",
        })
        constraint = {
            "id": "tier-must-constraint", "project": "fieldnote", "ticket": "T-tier",
            "kind": "constraint", "title": "Must-tier constraint",
            "claim": source["body"], "source_ids": [source["id"]],
            "status": "candidate", "valid_from": "2026-01-01",
        }
        fact = {
            "id": "tier-batch-fact", "project": "fieldnote", "ticket": "T-tier",
            "kind": "fact", "title": "Batch-tier fact",
            "claim": "Ticket-scoped fact for batch tier.", "source_ids": [source["id"]],
            "status": "candidate", "valid_from": "2026-01-01",
        }
        self.request("/api/memories", {"memories": [constraint, fact]})
        info = self.request("/api/info?project=fieldnote&ticket=T-tier")
        tiers = {m["id"]: m["review_tier"] for m in info["memories"]}
        self.assertEqual(tiers["tier-must-constraint"], "must")
        self.assertEqual(tiers["tier-batch-fact"], "batch")
        inherited_tiers = {m["id"]: m.get("review_tier") for m in info["inherited_memories"]}
        for memory_id, tier in inherited_tiers.items():
            self.assertIn(tier, ("must", "batch"), memory_id)
        self.request("/api/memories", {"memories": [
            dict(constraint, expected_version=1, status="retracted"),
            dict(fact, expected_version=1, status="retracted"),
        ]})

    def test_b1_inbox_excludes_lab_governance(self):
        """B1: lab-wide candidates must not appear in the inbox layer stack.

        Baseline (pre-B1): renderLayers mapped all workbench.layers including lab;
        desk redirected lab rows to Standing rules dialog (duplicate surface).

        Target: inboxLayers() omits kind==='lab'; dialog remains sole lab confirm path.
        """
        page = self.request("/")
        self.assertIn("function inboxLayers()", page)
        self.assertIn("layer.kind!=='lab'", page)
        source = self.request("/api/source", {
            "project": "__global__", "ticket": "", "confirm_global": True,
            "title": "Lab harness observation", "body": "Use .venv only for this project.",
        })
        candidate = {
            "id": "b1-harness-lab", "project": "__global__", "ticket": "",
            "kind": "standing_rule", "title": "Harness lab rule",
            "claim": source["body"], "source_ids": [source["id"]],
            "status": "candidate", "confirm_global": True, "valid_from": "2026-01-01",
        }
        self.request("/api/memories", {"memories": [candidate]})
        info = self.request("/api/info?project=fieldnote")
        inherited = [m for m in info["inherited_memories"]
                     if m.get("project") == "__global__" and m.get("status") == "candidate"]
        self.assertEqual(len(inherited), 1)
        self.assertEqual(inherited[0]["id"], "b1-harness-lab")
        confirmed = dict(candidate, expected_version=1, status="confirmed", confirm_global=True)
        self.request("/api/memories", {"memories": [confirmed]})
        history = self.request("/api/revisions/b1-harness-lab")["revisions"]
        self.assertEqual([m["status"] for m in history], ["candidate", "confirmed"])
        self.request("/api/memories", {"memories": [dict(confirmed, expected_version=2, status="retracted", confirm_global=True)]})
        self.assertIn("inboxLayers().map(layer=>", page)
        self.assertNotIn("shell.workbench.layers.map(layer=>", page)
        self.assertNotIn("data-standing>Add standing rule", page)

    def test_b3_create_dialog_replaces_prompt(self):
        """B3: header create flows use dialog + validation, not window.prompt."""
        page = self.request("/")
        self.assertNotIn("prompt(", page)
        self.assertIn('id="create-form"', page)
        self.assertIn("Choose a real workspace.", page)
        self.assertIn("Work item name is required.", page)
        self.assertIn("openCreateDialog('project')", page)
        self.assertIn("openCreateDialog('ticket'", page)
        self.assertIn("formId(event.target)==='create-form'", page)

    def test_b4_lab_tools_toggle_contract(self):
        """B4: Lab tools toggles agent preview only; layer stack stays visible in inbox-mode."""
        page = self.request("/")
        self.assertIn("body.inbox-mode .agent-preview{display:none}", page)
        self.assertNotRegex(page, r"body\.inbox-mode\s+\.layer-stack\{display:none\}")
        self.assertIn("document.body.classList.toggle('lab-mode',lab)", page)
        self.assertIn("document.body.classList.toggle('inbox-mode',!lab)", page)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Lab → Agent preview** toggles the agent preview column only", readme)
        self.assertIn("**Lab rules** button", readme)
        self.assertNotIn("Available under Lab tools", readme)
        self.assertNotIn("stay under Lab tools", readme)
        self.assertNotIn("first layer of the workspace", readme)
        self.assertNotIn("Lab tools still expose the layer stack", readme)
        self.assertNotIn("Lab → Standing rules", readme)

    def test_b5_empty_inbox_single_surface(self):
        """B5: fresh empty inbox is one card; caught-up still lists settled + evidence.

        Baseline: renderLayers mapped inboxLayers first, then prepended empty-inbox
        over still-rendered sections (0 to confirm Workspace bands under the hero).

        Target: early-return empty card only when there is nothing to browse
        (no waiting and no settled/sources). Caught-up falls through to the layer map
        and opens settled/evidence details when the waiting list is empty.
        """
        page = self.request("/")
        start = page.index("function canBrowseSettled(){")
        end = page.index("function renderBand(", start)
        body = page[start:end]
        self.assertIn("function canBrowseSettled(){", body)
        self.assertIn("if(!candidateRows().length&&!canBrowseSettled()){", body)
        self.assertIn("return;", body)
        self.assertNotIn("+$('layers').innerHTML", body)
        self.assertIn("openSettled=!candidateRows().length", body)
        empty_idx = body.index("if(!candidateRows().length&&!canBrowseSettled()){")
        map_idx = body.index("inboxLayers().map(layer=>")
        self.assertLess(empty_idx, map_idx)

    def test_bad_inputs_and_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.request("/api/compare", {"task": {"query": ""}})
        self.assertEqual(result.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.request("/api/source", {"project": "p", "title": "x", "body": "x"}, {"Origin": "https://unrelated.example"})
        self.assertEqual(result.exception.code, 403)

    def test_mcp_subprocess(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"cwd": "/tmp", "task": {"project": "fieldnote", "query": "Change button color", "as_of": "2026-09-12"}}}}]
        result = subprocess.run([sys.executable, "-m", "context_lab", "--db", self.db_path, "mcp"],
                                cwd=ROOT, input="\n".join(map(json.dumps, messages)) + "\n", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertFalse(response[-1]["result"]["isError"])
        self.assertIn("C-brand", response[-1]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
