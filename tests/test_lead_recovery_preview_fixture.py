"""In-process fixture confirms payload adaptation is not route activation."""
import unittest

from automation.monitoring.github_webhook import adapt_payload


class LeadContinuityFixtureTests(unittest.TestCase):
    def test_exact_head_payload_is_adapted_without_delivery_envelope(self):
        head = "a" * 40
        payload = {"action": "completed", "repository": {"full_name": "its-nix/os"},
                   "check_run": {"id": 77, "status": "completed", "conclusion": "success",
                                 "head_sha": head, "pull_requests": [{"number": 49}]}}
        signal = adapt_payload("check_run", payload, approved_repository="its-nix/os")
        self.assertEqual(signal["head_sha"], head)
        self.assertEqual(signal["pr_number"], 49)
        self.assertNotIn("delivered", signal)
        self.assertNotIn("correlation_key", signal)


if __name__ == "__main__":
    unittest.main()
