import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lab.engine import DATA_ROOT, applicability, compile_context, estimated_tokens, plan_task
from context_lab.evaluate import evaluate, score_packet
from context_lab.mcp import serve_mcp
from context_lab.provider import ModelEndpoint
from context_lab.service import compare
from context_lab.store import Store


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.seed(DATA_ROOT / "memories.json")

    def tearDown(self):
        self.store.close()

    def task(self, query="Add retry for note writes.", **extra):
        return dict(query=query, project="fieldnote", as_of="2026-09-12", **extra)

    def packet(self, query="Add retry for note writes.", strategy="targeted", **extra):
        return compile_context(self.store, self.task(query, **extra), strategy, persist=False)

    @staticmethod
    def ids(p):
        return {m["id"] for m in p["selected"]}

    def test_source_immutable(self):
        s = self.store.source("src-product")
        self.store.add_source(s)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.add_source(dict(s, body="Different history"))

    def test_source_quote_must_exist(self):
        m = self.store.memory("L-operation")
        with self.assertRaisesRegex(ValueError, "exact excerpt"):
            self.store.put_memories([dict(m, id="new-lesson", quote="Invented exact quote")])
        self.assertIsNone(self.store.memory("new-lesson"))

    def test_optimistic_revision_and_history(self):
        m = self.store.memory("C-brand")
        self.store.put_memories([dict(m, expected_version=1, claim="Use dark green.")])
        with self.assertRaisesRegex(ValueError, "Revision conflict"):
            self.store.put_memories([dict(m, expected_version=1, claim="Stale write")])
        history = self.store.revisions(m["id"])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["claim"], m["claim"])

    def test_batch_rolls_back(self):
        m = self.store.memory("C-brand")
        first = dict(m, id="fresh")
        bad = dict(m, id="bad", depends_on=["does-not-exist"])
        with self.assertRaises(ValueError):
            self.store.put_memories([first, bad])
        self.assertIsNone(self.store.memory("fresh"))

    def test_dependency_cycle_rejected(self):
        m = self.store.memory("C-brand")
        with self.assertRaisesRegex(ValueError, "Cycle"):
            self.store.put_memories([dict(m, id="x", depends_on=["y"]), dict(m, id="y", depends_on=["x"])])

    def test_cross_project_evidence_and_moves_rejected(self):
        m = self.store.memory("C-brand")
        with self.assertRaisesRegex(ValueError, "same project"):
            self.store.put_memories([dict(m, id="x", source_ids=["src-shop"])])
        with self.assertRaisesRegex(ValueError, "cannot move"):
            self.store.put_memories([dict(m, expected_version=1, project="shopfront", source_ids=["src-shop"])])

    def test_all_arms_share_scope_and_candidate_filters(self):
        for arm in ("retrieval", "lessons", "targeted"):
            p = self.packet("Explain the SQLite hosted database storage", strategy=arm, state={"single_device": True})
            self.assertNotIn("S-hosting", self.ids(p))
            self.assertNotIn("S-hosting", {t["id"] for t in p["trace"]})
            self.assertNotIn("D-unreviewed", self.ids(p))

    def test_current_and_historical_supersession(self):
        for arm in ("retrieval", "lessons", "targeted"):
            current = self.packet("Explain the login plan", strategy=arm)
            old = compile_context(self.store, dict(self.task("Explain the login plan"), as_of="2026-02-01"), arm, persist=False)
            self.assertIn("D-auth-current", self.ids(current))
            self.assertNotIn("D-auth-old", self.ids(current))
            self.assertIn("D-auth-old", self.ids(old))
            self.assertNotIn("D-auth-current", self.ids(old))

    def test_expiry_and_future(self):
        self.assertNotIn("C-expired", self.ids(self.packet("Change the button color")))
        self.assertNotIn("C-future", self.ids(self.packet("Prepare the release")))

    def test_exception_disables_lesson(self):
        p = self.packet(state={"idempotency_verified": True, "http_method": "POST"})
        self.assertNotIn("L-operation", self.ids(p))
        self.assertNotIn("L-read", self.ids(p))

    def test_missing_and_null_exception_are_conditional(self):
        for state in ({"http_method": "POST"}, {"http_method": "POST", "idempotency_verified": None}):
            p = self.packet(state=state)
            self.assertIn("L-operation", self.ids(p))
            self.assertEqual(p["needs"][0]["status"], "conditional")

    def test_boolean_is_not_numeric(self):
        m = self.store.memory("L-operation")
        check = applicability(m, plan_task(self.task(state={"idempotency_verified": 1})))
        self.assertNotEqual(check[0], "inapplicable")

    def test_dependency_included_with_lesson(self):
        p = self.packet(state={"http_method": "POST", "idempotency_verified": False})
        self.assertTrue({"L-operation", "E-duplicate"} <= self.ids(p))

    def test_missing_active_dependency_blocks_lesson(self):
        dep = self.store.memory("E-duplicate")
        self.store.put_memories([dict(dep, expected_version=1, status="retracted")])
        p = self.packet(state={"http_method": "POST", "idempotency_verified": False})
        self.assertNotIn("L-operation", self.ids(p))
        self.assertEqual(p["dependency_gaps"][0]["missing"], ["E-duplicate"])

    def test_budget_never_truncates_a_required_bundle(self):
        task = self.task(state={"http_method": "POST", "idempotency_verified": False})
        for budget in (200, 300, 400, 800, 1200):
            p = compile_context(self.store, task, budget=budget, persist=False)
            self.assertLessEqual(estimated_tokens(p["context"]), budget)
            if "L-operation" in self.ids(p):
                self.assertIn("E-duplicate", self.ids(p))

    def test_impossible_header_budget_fails(self):
        with self.assertRaisesRegex(ValueError, "exceed budget"):
            compile_context(self.store, self.task("x" * 2000), budget=128)

    def test_changed_assumption_is_not_silently_accepted(self):
        p = self.packet("Add team sharing", state={"single_device": False})
        statuses = {n["need"]: n["status"] for n in p["needs"]}
        self.assertEqual(statuses["storage_assumption"], "conditional")
        self.assertEqual(statuses["access_policy"], "missing")
        self.assertTrue(any("RECONSIDER" in x for x in p["warnings"]))

    def test_conflicts_prevent_evidence_present(self):
        p = self.packet("Implement deleted-note retention")
        self.assertEqual(p["needs"][0]["status"], "conflicted")
        self.assertEqual(p["conflicts"][0]["key"], "retention_days")

    def test_no_match_is_not_sufficient(self):
        p = self.packet("Let colleagues write together")
        self.assertEqual(p["needs"], [])
        self.assertIn("no task action recognized", p["warnings"][0].lower())

    def test_caller_supplied_features_work_for_new_action(self):
        s = self.store.add_source({"id": "src-custom", "project": "custom", "title": "Rare operating constraint", "body": "Do not move a fragile sample while its seal is open."})
        self.store.put_memories([{"id": "custom-rule", "project": "custom", "kind": "constraint", "title": "Seal requirement", "claim": s["body"], "source_ids": [s["id"]], "status": "confirmed", "valid_from": "2026-01-01", "applies": {"actions_any": ["transport"]}, "need_tags": ["seal_status"]}])
        p = compile_context(self.store, {"project": "custom", "query": "Take this to the next room", "actions": ["transport"], "needs": ["seal_status"]}, persist=False)
        self.assertEqual(self.ids(p), {"custom-rule"})

    def test_feedback_uses_run_snapshot(self):
        p = compile_context(self.store, self.task(state={"http_method": "POST", "idempotency_verified": False}))
        f = self.store.log_feedback(p["run_id"], "L-operation", "missed", "Agent ignored it")
        self.assertEqual(f["diagnosis"], "supplied_but_reported_missed")
        self.assertEqual(self.store.log_feedback(p["run_id"], "unknown", "missed")["diagnosis"], "not_recorded_at_run")
        self.assertEqual(self.store.log_feedback(p["run_id"], "L-read", "missed")["diagnosis"], "eligibility_or_applicability")

    def test_retrieval_miss_diagnosis(self):
        p = compile_context(self.store, self.task("Adjust the editor spacing"), strategy="retrieval")
        self.assertEqual(self.store.log_feedback(p["run_id"], "C-brand", "missed")["diagnosis"], "retrieval")

    def test_comparison_preserves_actual_planning_method(self):
        r = compare(self.store, {"task": self.task()})
        self.assertTrue(all(p["task"]["planning"]["method"] == "explicit_regex_rules" for p in r["packets"]))

    def test_labels_do_not_change_retrieval(self):
        p = self.packet("Change button color")
        one = score_packet(p, {"required": ["C-brand"]})
        two = score_packet(p, {"required": ["not-there"]})
        self.assertNotEqual(one["required_recall"], two["required_recall"])
        self.assertEqual(self.ids(p), {"C-brand"})

    def test_mcp_roundtrip_and_errors(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"task": self.task("Change button color")}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory_source", "arguments": {"source_id": "src-shop", "project": "fieldnote"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "unknown"}}]
        out = io.StringIO()
        serve_mcp(self.store, io.StringIO("\n".join(map(json.dumps, msgs))), out)
        results = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(len(results), 5)
        self.assertIn("memory_initiate", {t["name"] for t in results[1]["result"]["tools"]})
        data = json.loads(results[2]["result"]["content"][0]["text"])
        self.assertIn("C-brand", data["context"])
        self.assertTrue(results[3]["result"]["isError"])
        self.assertEqual(results[4]["error"]["code"], -32602)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.model = ModelEndpoint(self.store, "http://127.0.0.1:12345/v1", "test-model", "test-embed")

    def tearDown(self):
        self.store.close()

    def test_embedding_cache_and_order(self):
        reply = {"data": [{"index": 1, "embedding": [0., 1.]}, {"index": 0, "embedding": [1., 0.]}]}
        with patch.object(self.model, "request", return_value=reply) as call:
            self.assertEqual(self.model.embed(["a", "b"]), [[1., 0.], [0., 1.]])
            self.assertEqual(self.model.embed(["b", "a"]), [[0., 1.], [1., 0.]])
            self.assertEqual(call.call_count, 1)

    def test_draft_cannot_fabricate_source_quote(self):
        source = {"id": "s", "project": "p", "body": "The observed fact."}
        with patch.object(self.model, "complete", return_value={"lessons": [{"quote": "Invented fact"}]}):
            with self.assertRaisesRegex(ValueError, "not found verbatim"):
                self.model.draft(source)

    def test_valid_draft_is_not_saved_or_confirmed(self):
        source = {"id": "s", "project": "p", "body": "The observed fact."}
        reply = {"lessons": [{"title": "Lesson", "claim": "A candidate conclusion", "quote": "The observed fact.", "actions_any": ["sync"], "need_tags": ["offline_mode"]}]}
        with patch.object(self.model, "complete", return_value=reply):
            draft = self.model.draft(source)
        self.assertEqual(draft["memories"][0]["status"], "candidate")
        self.assertEqual(self.store.memories(), [])

    def test_invalid_model_plan_rejected(self):
        from context_lab.engine import catalog
        with patch.object(self.model, "complete", return_value={"actions": ["made-up"], "needs": []}):
            with self.assertRaisesRegex(ValueError, "unknown"):
                self.model.plan({"query": "a"}, catalog())


if __name__ == "__main__":
    unittest.main()
