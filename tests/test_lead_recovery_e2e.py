"""In-process contract preview only; no route, wake, or recovery execution."""
import unittest

from automation.monitoring.github_signals import normalize_signal, lead_route_envelope, accept_once


class LeadContinuityFixtureTests(unittest.TestCase):
    def test_exact_head_signal_produces_preview_not_delivery(self):
        head = "a" * 40
        signal = normalize_signal({"kind": "check", "check_run_id": 77, "conclusion": "success",
                                  "head_sha": head}, repository="its-nix/os", pr_number=49)
        route = lead_route_envelope(signal, parent_task_id="parent-fixture",
                                    lead_profile="pulse", cron_job="continuity-fixture")
        self.assertEqual(route["evidence"]["head_sha"], head)
        self.assertTrue(route["preview_only"])
        self.assertEqual(route["route_profile"], "pulse")
        self.assertNotIn("delivered", route)

    def test_in_process_seen_set_is_not_durable_delivery_suppression(self):
        signal = normalize_signal({"kind": "review", "review_id": 8, "state": "approved",
                                  "head_sha": "b" * 40}, repository="its-nix/os", pr_number=49)
        seen = set()
        self.assertTrue(accept_once(signal, seen))
        self.assertFalse(accept_once(signal, seen))
        # A fresh process/set accepts the same fixture again.
        self.assertTrue(accept_once(signal, set()))


if __name__ == "__main__":
    unittest.main()
