import unittest

from context_lab.agent_api import propose
from context_lab.mcp import call
from context_lab.store import Store, obvious_secret_kind


class ObserveSecretTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.add_source({
            "id": "src-1", "project": "app", "ticket": "T-1",
            "title": "baseline", "body": "Ticket evidence for secret gate tests.",
        })

    def tearDown(self):
        self.store.close()

    def _observe(self, body, **extra):
        payload = {
            "project": "app", "ticket": "T-1",
            "title": "Observation", "body": body,
            **extra,
        }
        return self.store.add_source(payload)

    def test_refuses_bearer_token(self):
        with self.assertRaisesRegex(ValueError, r"secret \(bearer_token\)"):
            self._observe("Authorization header was Bearer " + "a" * 24)

    def test_refuses_openai_token(self):
        with self.assertRaisesRegex(ValueError, r"secret \(openai_token\)"):
            self._observe("Found sk-" + "a" * 24 + " in logs")

    def test_refuses_openai_sk_live_token(self):
        with self.assertRaisesRegex(ValueError, r"secret \(openai_token\)"):
            self._observe("Found sk-live-abcdefghijklmnopqrstuvwxyz1234567890 in logs")

    def test_refuses_github_pat(self):
        with self.assertRaisesRegex(ValueError, r"secret \(github_token\)"):
            self._observe("Leaked ghp_" + "a" * 36)

    def test_refuses_github_pat_prefix(self):
        with self.assertRaisesRegex(ValueError, r"secret \(github_token\)"):
            self._observe("Leaked github_pat_" + "a" * 24)

    def test_refuses_aws_access_key(self):
        with self.assertRaisesRegex(ValueError, r"secret \(aws_access_key\)"):
            self._observe("Key AKIAIOSFODNN7EXAMPLE rotated")

    def test_refuses_private_key_pem(self):
        with self.assertRaisesRegex(ValueError, r"secret \(private_key\)"):
            self._observe("-----BEGIN RSA PRIVATE KEY-----\nMIIE")

    def test_refuses_assigned_secret(self):
        with self.assertRaisesRegex(ValueError, r"secret \(assigned_secret\)"):
            self._observe('api_key = "' + "x" * 24 + '"')

    def test_allows_api_key_discussion_in_body(self):
        saved = self._observe("Rotate the api_key quarterly per runbook.")
        self.assertEqual(saved["body"], "Rotate the api_key quarterly per runbook.")

    def test_propose_allows_api_key_in_title_and_claim(self):
        propose(self.store, [{
            "project": "app", "ticket": "T-1", "kind": "lesson",
            "title": "api_key handling",
            "claim": "Never log api_key values",
            "source_ids": ["src-1"],
        }])
        self.assertEqual(self.store.memories(project="app", ticket="T-1")[-1]["title"], "api_key handling")

    def test_mcp_observe_raises_same_as_store(self):
        with self.assertRaisesRegex(ValueError, r"secret \(openai_token\)"):
            call(self.store, "memory_observe", {
                "project": "app", "ticket": "T-1",
                "title": "Leak", "body": "sk-" + "a" * 24,
            })

    def test_propose_same_claim_new_id_succeeds(self):
        draft = {
            "project": "app", "ticket": "T-1", "kind": "lesson",
            "title": "First", "claim": "same text", "source_ids": ["src-1"],
        }
        propose(self.store, [dict(draft, id="mem-a")])
        propose(self.store, [dict(draft, id="mem-b")])
        self.assertIsNotNone(self.store.memory("mem-a"))
        self.assertIsNotNone(self.store.memory("mem-b"))

    def test_obvious_secret_kind_returns_none_for_prose(self):
        self.assertIsNone(obvious_secret_kind("Discuss api_key hygiene in runbooks"))


if __name__ == "__main__":
    unittest.main()
