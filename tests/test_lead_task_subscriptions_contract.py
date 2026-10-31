"""Lead roles subscribe separately to dependency-linked native tasks."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]

class LeadTaskSubscriptionContractTests(unittest.TestCase):
    def test_each_lead_role_requires_separate_task_subscriptions(self):
        for lead in ("pulse", "algua", "meabeclick"):
            with self.subTest(lead=lead):
                text = (ROOT / "profiles" / lead / "ROLE.md").read_text()
                for required in (
                    "spec-level objective", "every execution task", "notify-subscribe",
                    "notify-list", "--json", "notify+wake", "--thread-id",
                    "--parent-chat-id", "--guild-id", "--chat-type",
                    "owning-lead-profile", "Re-read canonical native task state",
                    "<execution_task_id> <objective_task_id>", "Record execution IDs",
                    "directly to Lior", "subscribe separately to the objective and every execution task",
                    "native task links represent blocking dependencies, not containment",
                ):
                    self.assertIn(required, text)
                self.assertNotIn("hermes kanban link parent", text)

if __name__ == "__main__":
    unittest.main()
