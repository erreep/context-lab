"""Import structure (heading_path, links, properties) and action-match variants."""
import tempfile
import unittest
from pathlib import Path

from context_lab.engine import applicability, plan_task, vocabulary
from context_lab.knowledge import extract_links, initiate, sections, split_frontmatter
from context_lab.store import Store


class ImportStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.store = Store(str(self.root / "memory.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_frontmatter_heading_path_and_links(self):
        (self.notes / "guide.md").write_text(
            "---\nstatus: draft\nowner: sebas\n---\n"
            "# Architecture\n"
            "## Selection\n"
            "See [[budget-notes]] and [docs](https://example.com/a).\n"
            "Upload retries preserve identity.\n",
            encoding="utf-8",
        )
        (self.notes / "Cl").mkdir()
        (self.notes / "Cl" / "session.md").write_text("# skip me\n", encoding="utf-8")
        result = initiate(self.store, "app", "T-1", path=str(self.notes))
        self.assertEqual(result["status"], "initialized")
        docs = self.store.documents("app", "T-1")
        self.assertTrue(docs)
        hit = next(d for d in docs if d["heading"] == "Selection")
        self.assertEqual(hit["heading_path"], ["Architecture", "Selection"])
        self.assertIn("budget-notes", hit["outbound_links"])
        self.assertIn("https://example.com/a", hit["outbound_links"])
        self.assertEqual(hit["properties"].get("status"), "draft")
        self.assertEqual(hit["properties"].get("owner"), "sebas")
        self.assertTrue(all("Cl/" not in d["path"] for d in docs))

    def test_refresh_idempotent_chunk_count(self):
        (self.notes / "a.md").write_text("# One\nhello\n", encoding="utf-8")
        initiate(self.store, "app", "T-1", path=str(self.notes))
        first = self.store.knowledge_base("app", "T-1")["chunk_count"]
        initiate(self.store, "app", "T-1", path=str(self.notes), refresh=True)
        second = self.store.knowledge_base("app", "T-1")["chunk_count"]
        self.assertEqual(first, second)

    def test_unknown_paraphrase_does_not_force_inapplicable(self):
        task = plan_task({"project": "app", "query": "please do the thing with the widgets carefully"})
        self.assertEqual(task["planning"]["action_match"], "UnknownParaphrase")
        self.assertEqual(task["actions"], [])
        memory = {"applies": {"actions_any": ["commit"]}, "unless": {}, "assumptions": {}}
        status, reasons, uncertainties = applicability(memory, task)
        self.assertNotEqual(status, "inapplicable")
        self.assertTrue(any("UnknownParaphrase" in u for u in uncertainties))

    def test_known_incompatible_still_excludes(self):
        task = plan_task({"project": "app", "query": "x", "actions": ["commit"]})
        memory = {"applies": {"actions_any": ["deploy"]}, "unless": {}, "assumptions": {}}
        status, reasons, _ = applicability(memory, task)
        self.assertEqual(status, "inapplicable")
        self.assertTrue(any("KnownIncompatible" in r for r in reasons))

    def test_vocabulary_includes_confirmed_need_tags(self):
        src = self.store.add_source({"project": "app", "ticket": "", "title": "t", "body": "payload budget"})
        self.store.put_memories([{
            "id": "m1", "project": "app", "ticket": "", "kind": "lesson", "status": "confirmed",
            "title": "Budget wire", "claim": "Budget the full payload", "source_ids": [src["id"]],
            "need_tags": ["payload_budget"], "applies": {"actions_any": ["compile_context"]},
        }])
        vocab = vocabulary(self.store, "app")
        self.assertIn("payload_budget", vocab["needs"])
        self.assertIn("compile_context", vocab["actions"])

    def test_helpers(self):
        props, body = split_frontmatter("---\nk: v\n---\n# Hi\n")
        self.assertEqual(props["k"], "v")
        self.assertTrue(body.startswith("# Hi"))
        paths = list(sections("# A\n## B\ntext\n"))
        self.assertEqual(paths[0][0], ["A"])
        self.assertEqual(paths[1][0], ["A", "B"])
        self.assertEqual(extract_links("[[x]] and [y](z)"), ["x", "z"])


if __name__ == "__main__":
    unittest.main()
