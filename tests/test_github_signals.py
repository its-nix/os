import unittest

from automation.monitoring.github_signals import normalize_signal


class GitHubSignalTests(unittest.TestCase):
    SHA = "a" * 40

    def test_check_status_changes_and_head_identity(self):
        base = {"kind": "check", "check_run_id": 1234, "head_sha": self.SHA}
        queued = normalize_signal({**base, "status": "queued"}, repository="its-nix/os", pr_number=49)
        success = normalize_signal({**base, "status": "completed", "conclusion": "success"}, repository="its-nix/os", pr_number=49)
        next_head = normalize_signal({**base, "head_sha": "b" * 40, "conclusion": "success"}, repository="its-nix/os", pr_number=49)
        self.assertEqual(queued["head_sha"], self.SHA)
        self.assertEqual(success["status"], "success")
        self.assertNotEqual(queued["head_sha"], next_head["head_sha"])

    def test_review_status_is_normalized_without_claiming_delivery_deduplication(self):
        payload = {"kind": "review", "review_id": 77, "state": "approved", "head_sha": self.SHA}
        first = normalize_signal(payload, repository="its-nix/os", pr_number=49)
        self.assertEqual(first["review_id"], "77")
        self.assertNotIn("event_key", first)

    def test_rejects_unbound_or_malformed_evidence(self):
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success"}, repository="its-nix/os", pr_number=49)
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success", "head_sha": "x"}, repository="its-nix/os", pr_number=49)
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success", "head_sha": "a" * 41}, repository="its-nix/os", pr_number=49)


if __name__ == "__main__":
    unittest.main()
