"""review_tier: presentation blast-radius from existing memory fields."""
import unittest

from context_lab.schemas import review_tier


class ReviewTierTests(unittest.TestCase):
    def test_lab_wide_and_standing_are_must(self):
        self.assertEqual(
            review_tier({"project": "__global__", "ticket": "", "kind": "fact"}),
            "must",
        )
        self.assertEqual(
            review_tier({"project": "app", "ticket": "T-1", "kind": "standing_rule"}),
            "must",
        )

    def test_baseline_and_constraints_are_must(self):
        self.assertEqual(
            review_tier({"project": "app", "ticket": "", "kind": "lesson"}),
            "must",
        )
        self.assertEqual(
            review_tier({"project": "app", "ticket": "T-1", "kind": "constraint"}),
            "must",
        )

    def test_ticket_facts_are_batch(self):
        for kind in ("fact", "event", "decision", "lesson"):
            self.assertEqual(
                review_tier({"project": "app", "ticket": "T-1", "kind": kind}),
                "batch",
                kind,
            )

    def test_missing_fields_fail_closed_to_must(self):
        self.assertEqual(review_tier({}), "must")
        self.assertEqual(review_tier(None), "must")


if __name__ == "__main__":
    unittest.main()
