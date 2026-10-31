"""Lead roles must install and verify subscriptions for every native task."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class LeadTaskSubscriptionContractTests(unittest.TestCase):
 def test_each_lead_role_requires_parent_and_child_subscription_readback(self):
  for lead in ("pulse","algua","meabeclick"):
   with self.subTest(lead=lead):
    text=(ROOT/"profiles"/lead/"ROLE.md").read_text()
    for required in ("spec-level parent","every execution child","notify-subscribe","notify-list","--json","notify+wake","--thread-id","--parent-chat-id","--guild-id","--chat-type","owning-lead-profile","Re-read canonical native task state"): self.assertIn(required,text)
if __name__=="__main__": unittest.main()
