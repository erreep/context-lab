import unittest

from context_lab.mcp import TOOLS
from context_lab.schemas import GATE_TEXT

UTTERANCES = (
    ("u1", "project", "named_key"),
    ("u2", "cwd", "path"),
    ("u3", "ticket", "work_item_id"),
    ("u4", "ticket", "work_item_id"),
    ("u5", "cwd", "path"),
)


def tool_fields():
    names = set()
    for tool in TOOLS:
        names.update(tool["inputSchema"].get("properties") or {})
    return names


class AgentVocabTests(unittest.TestCase):
    def test_tools_have_no_workspace_field(self):
        self.assertNotIn("workspace", tool_fields())

    def test_gate_text_maps_human_scope_words(self):
        gate = GATE_TEXT.lower()
        names = tool_fields()
        for uid, field, kind in UTTERANCES:
            with self.subTest(uid):
                if field == "project" and kind == "named_key":
                    self.assertIn("workspace", gate)
                    self.assertIn("project", gate)
                elif uid == "u5":
                    self.assertIn("project", gate)
                    self.assertTrue(
                        "not folder path" in gate
                        or "never `project`" in gate
                        or "≠ `project`" in gate
                        or "never project" in gate,
                        "GATE_TEXT must reject a path as project",
                    )
                elif field == "cwd":
                    self.assertTrue(any(word in gate for word in ("folder", "repo", "cwd")))
                    self.assertIn("project", gate)
                elif field == "ticket":
                    if "work_item" in names:
                        continue
                    self.assertTrue("work item" in gate or "work_item" in gate)
                    self.assertIn("ticket", gate)
                else:
                    self.fail(f"unknown {field}/{kind}")


if __name__ == "__main__":
    unittest.main()
