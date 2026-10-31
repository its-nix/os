"""Lead decisions stay in the owning Lead; no parallel decision engine."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LeadDecisionBoundaryTests(unittest.TestCase):
    def test_custom_decision_engine_is_absent(self):
        self.assertFalse((ROOT / "automation/monitoring/lead_decision.py").exists())

    def test_existing_recovery_is_only_a_pure_monitoring_planner(self):
        source = (ROOT / "automation/monitoring/recovery.py").read_text(encoding="utf-8")
        self.assertIn("approval_required", source)
        self.assertIn("requires_human_approval", source)
        self.assertNotIn("sqlite3.connect", source)


if __name__ == "__main__":
    unittest.main()
