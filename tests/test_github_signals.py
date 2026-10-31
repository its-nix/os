import unittest

from automation.monitoring.github_signals import accept_once, lead_route_envelope, normalize_signal


class GitHubSignalTests(unittest.TestCase):
    SHA = "a" * 40

    def test_check_status_changes_and_head_identity(self):
        base = {"kind": "check", "check_run_id": 1234, "head_sha": self.SHA}
        queued = normalize_signal({**base, "status": "queued"}, repository="its-nix/os", pr_number=49)
        success = normalize_signal({**base, "status": "completed", "conclusion": "success"}, repository="its-nix/os", pr_number=49)
        next_head = normalize_signal({**base, "head_sha": "b" * 40, "conclusion": "success"}, repository="its-nix/os", pr_number=49)
        self.assertEqual(queued["head_sha"], self.SHA)
        self.assertEqual(success["status"], "success")
        self.assertNotEqual(queued["event_key"], success["event_key"])
        self.assertNotEqual(success["event_key"], next_head["event_key"])

    def test_review_status_and_repeated_delivery_dedupe(self):
        payload = {"kind": "review", "review_id": 77, "state": "approved", "head_sha": self.SHA}
        first = normalize_signal(payload, repository="its-nix/os", pr_number=49)
        repeated = normalize_signal(dict(payload), repository="its-nix/os", pr_number=49)
        seen = set()
        self.assertEqual(first, repeated)
        self.assertTrue(accept_once(first, seen))
        self.assertFalse(accept_once(repeated, seen))

    def test_profile_route_envelope_is_exact_head_parent_scoped_and_idempotent(self):
        payload = {"kind": "review", "review_id": 77, "state": "approved", "head_sha": self.SHA}
        signal = normalize_signal(payload, repository="example/repo", pr_number=14)
        first = lead_route_envelope(
            signal, parent_task_id="objective-14", lead_profile="pulse", cron_job="lead-reconcile"
        )
        replay = lead_route_envelope(
            normalize_signal(dict(payload), repository="example/repo", pr_number=14),
            parent_task_id="objective-14", lead_profile="pulse", cron_job="lead-reconcile"
        )
        self.assertEqual(first, replay)
        self.assertEqual(first["evidence"]["head_sha"], self.SHA)
        self.assertEqual(first["route_profile"], "pulse")
        self.assertTrue(first["preview_only"])
        self.assertEqual(first["idempotency_key"], replay["idempotency_key"])
        other_parent = lead_route_envelope(
            signal, parent_task_id="objective-15", lead_profile="pulse", cron_job="lead-reconcile"
        )
        self.assertNotEqual(first["idempotency_key"], other_parent["idempotency_key"])

    def test_rejects_unbound_or_malformed_evidence(self):
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success"}, repository="its-nix/os", pr_number=49)
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success", "head_sha": "x"}, repository="its-nix/os", pr_number=49)
        with self.assertRaises(ValueError):
            normalize_signal({"kind": "check", "check_run_id": 1, "status": "success", "head_sha": "a" * 41}, repository="its-nix/os", pr_number=49)


if __name__ == "__main__":
    unittest.main()
