import tempfile
import unittest
from pathlib import Path

from automation.monitoring.lead_decision import reconcile, Refusal

MANIFEST = {"projects": [{"id": "p", "lead": "lead", "recovery_action": "retry"}],
            "profiles": [{"id": "lead", "kind": "lead"}]}
ROUTES = {"lead": {"profile": "lead", "platform": "discord", "chat_id": "12345"}}
OBS = {"observed_at": "2026-10-31T13:30:00+00:00", "tasks": [],
       "exited_workers": [{"project_id": "p", "id": "worker-1", "status": "crashed", "stderr": "secret"}]}


class LeadDecisionTests(unittest.TestCase):
    def test_missed_event_reconciles_worker_exit_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.db"
            first = reconcile(OBS, MANIFEST, routes=ROUTES, state_path=state)
            second = reconcile(OBS, MANIFEST, routes=ROUTES, state_path=state)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["decision"]["kind"], "approval_escalation")
            self.assertEqual(first[0]["decision"]["evidence"]["type"], "worker_exit")
            self.assertNotIn("secret", str(first))
            self.assertEqual(second, [])

    def test_only_explicit_allowlist_yields_proposal_never_execution(self):
        with tempfile.TemporaryDirectory() as d:
            out = reconcile(OBS, MANIFEST, routes=ROUTES, state_path=Path(d) / "s.db",
                            authorized_actions={"p": ["retry"]})
            self.assertEqual(out[0]["decision"], {"kind": "authorized_recovery_proposal", "action": "retry", "requires_human": False})
            self.assertTrue(out[0]["preview_only"])

    def test_bad_route_and_unbounded_retry_refuse_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Refusal):
                reconcile(OBS, MANIFEST, routes={}, state_path=Path(d) / "s.db")
            with self.assertRaises(Refusal):
                reconcile(OBS, MANIFEST, routes=ROUTES, state_path=Path(d) / "s.db", retry_limit=99)


if __name__ == "__main__":
    unittest.main()
