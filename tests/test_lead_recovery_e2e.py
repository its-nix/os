"""Injected preview integration across signal normalization and Lead decisions."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.monitoring.continuity import normalize
from automation.monitoring.github_signals import normalize_signal, lead_route_envelope, accept_once
from automation.monitoring.lead_decision import reconcile

HEAD = "a" * 40
MANIFEST = {"projects": [{"id": "p", "lead": "pulse", "recovery_action": "retry"}],
            "profiles": [{"id": "pulse", "kind": "lead"}]}
ROUTES = {"pulse": {"profile": "pulse", "platform": "discord", "chat_id": "12345"}}
NOW = datetime.now(timezone.utc)


class RecoveryEndToEndTests(unittest.TestCase):
    def test_child_review_events_resume_exact_head_and_recovery_preview(self):
        observed = NOW.isoformat()
        obs = {"observed_at": observed,
               "tasks": [{"project_id": "p", "id": "child-1", "status": "failed"},
                         {"project_id": "p", "id": "review-1", "status": "review"}],
               "checks": [{"project_id": "p", "repo": "its-nix/os", "pr_number": 49,
                           "pr_state": "OPEN", "head_sha": HEAD, "observed_head_sha": HEAD,
                           "conclusion": "SUCCESS"}]}
        events = normalize(obs, MANIFEST, now=NOW)
        self.assertEqual({e["identity"]["type"] for e in events}, {"kanban_terminal", "github_check"})
        github = normalize_signal({"kind": "check", "check_run_id": 77, "conclusion": "success", "head_sha": HEAD}, repository="its-nix/os", pr_number=49)
        route = lead_route_envelope(github, parent_task_id="parent-1", lead_profile="pulse", cron_job="continuity")
        seen = set()
        self.assertTrue(accept_once(github, seen))
        self.assertFalse(accept_once(normalize_signal({"kind": "check", "check_run_id": 77, "conclusion": "success", "head_sha": HEAD}, repository="its-nix/os", pr_number=49), seen))
        self.assertEqual(route["evidence"]["head_sha"], HEAD)
        self.assertTrue(route["preview_only"])
        with tempfile.TemporaryDirectory() as d:
            decisions = reconcile(obs, MANIFEST, routes=ROUTES, state_path=Path(d)/"state.db", authorized_actions={"p": ["retry"]})
            replay = reconcile(obs, MANIFEST, routes=ROUTES, state_path=Path(d)/"state.db", authorized_actions={"p": ["retry"]})
        self.assertEqual(len(decisions), 3)
        self.assertEqual(replay, [])
        self.assertTrue(all(x["decision"]["kind"] == "authorized_recovery_proposal" and x["preview_only"] for x in decisions))

    def test_missed_event_reconciliation_and_protected_approval_gate(self):
        obs = {"observed_at": NOW.isoformat(), "tasks": [], "exited_workers": [
            {"project_id": "p", "id": "worker-9", "status": "crashed", "stderr": "must-not-leak"}]}
        with tempfile.TemporaryDirectory() as d:
            out = reconcile(obs, MANIFEST, routes=ROUTES, state_path=Path(d)/"state.db", authorized_actions={})
            replay = reconcile(obs, MANIFEST, routes=ROUTES, state_path=Path(d)/"state.db", authorized_actions={})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["decision"]["kind"], "approval_escalation")
        self.assertTrue(out[0]["decision"]["requires_human"])
        self.assertNotIn("must-not-leak", str(out))
        self.assertEqual(replay, [])

    def test_retry_bound_is_validated_and_automatic_attempts_are_zero(self):
        obs = {"observed_at": NOW.isoformat(), "tasks": [{"project_id": "p", "id": "failed", "status": "failed"}]}
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/"state.db"
            once = reconcile(obs, MANIFEST, routes=ROUTES, state_path=state, authorized_actions={"p": ["retry"]}, retry_limit=1)
            twice = reconcile(obs, MANIFEST, routes=ROUTES, state_path=state, authorized_actions={"p": ["retry"]}, retry_limit=1)
        self.assertEqual(len(once), 1)
        self.assertEqual(twice, [])
        self.assertTrue(once[0]["preview_only"])
        with tempfile.TemporaryDirectory() as d, self.assertRaises(Exception):
            reconcile(obs, MANIFEST, routes=ROUTES, state_path=Path(d)/"state.db", retry_limit=6)


if __name__ == "__main__":
    unittest.main()
