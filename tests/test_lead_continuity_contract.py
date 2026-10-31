"""Contract checks tying project Leads to native Kanban continuity."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LeadContinuityContractTests(unittest.TestCase):
    def test_each_governed_project_lead_owns_native_parent_and_handoff_evidence(self):
        manifest = json.loads((ROOT / "nix-os.yaml").read_text(encoding="utf-8"))
        profiles = {p["id"]: p for p in manifest["profiles"]}
        self.assertEqual({"pulse", "algua", "meabeclick"}, {p["id"] for p in manifest["projects"]})
        for project in manifest["projects"]:
            with self.subTest(project=project["id"]):
                lead = profiles[project["lead"]]
                self.assertEqual("lead", lead["kind"])
                role = (ROOT / "profiles" / lead["id"] / "ROLE.md").read_text(encoding="utf-8")
                for required in (
                    "durable parent task", "native children", "worker terminal events",
                    "notify+wake", "duplicate or stale notifications", "exact full head SHA",
                    "CI run/check IDs", "review ID/state", "never create parallel Todoist tasks",
                    "hermes kanban notify-subscribe", "subscription cursor claims",
                    "unknown/partial", "decisions belong to you as Lead",
                ):
                    self.assertIn(required, role)

    def test_exact_head_evidence_remains_unknown_in_current_collector_contract(self):
        collector = (ROOT / "automation/monitoring/collector.py").read_text(encoding="utf-8")
        contract = (ROOT / "docs/LEAD-CONTINUITY-CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("statusCheckRollup", collector)
        self.assertIn("headRefOid", collector)
        self.assertIn("unknown/not available from this collector", contract)
        self.assertIn("CI run/check IDs and conclusions for that exact SHA", contract)

    def test_native_notifier_supports_review_and_worker_terminal_wakes(self):
        """Guard against role contracts drifting from supported Hermes event names."""
        source = Path.home() / ".hermes/hermes-agent/gateway/kanban_watchers_notifier.py"
        if not source.exists():
            self.skipTest("installed Hermes source unavailable")
        text = source.read_text(encoding="utf-8")
        for kind in ("completed", "blocked", "gave_up", "crashed", "timed_out",
                     "review_requested", "changes_requested"):
            with self.subTest(kind=kind):
                self.assertIn(f'"{kind}"', text)
        self.assertIn("notify+wake", text)
        self.assertIn("claim_unseen_events_for_sub", text)


if __name__ == "__main__":
    unittest.main()