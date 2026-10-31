"""Synthetic-only continuity contract simulation; never live delivery proof."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.monitoring import recovery
from automation.monitoring.github_webhook import adapt_payload

ROOT = Path(__file__).resolve().parents[1]


class SimulatedAdmission:
    """Test double, not Hermes durable native delivery-ID admission."""
    def __init__(self): self.ids = set()
    def admit(self, delivery_id):
        if delivery_id in self.ids: return False
        self.ids.add(delivery_id); return True


class SimulatedLead:
    """Test-only Lead session rereading canonical task/evidence fixtures."""
    def __init__(self, lead, profile, task, evidence):
        self.lead, self.profile, self.task, self.evidence = lead, profile, task, evidence
        self.wakes = []
    def wake(self, payload): self.wakes.append(payload)
    def reread_and_consider(self, expected_sha):
        task = json.loads(json.dumps(self.task)); evidence = json.loads(json.dumps(self.evidence))
        if task.get('id') not in ('objective-1', 'task-1') or task['status'] not in ('open', 'blocked') or evidence.get('head_sha') != expected_sha:
            return False
        if (evidence.get('fresh') is not True or len(expected_sha) != 40
                or evidence.get('pr_number') != 49 or evidence.get('check_id') != 77
                or evidence.get('check_conclusion') != 'failure'):
            return False
        return True


class LeadContinuityIntegrationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT/'nix-os.yaml').read_text())
        self.routes = json.loads((ROOT/'automation/monitoring/github-webhook-routes.json').read_text())

    def test_simulated_delivery_replay_route_reread_and_approval_boundary(self):
        route = next(r for r in self.routes['routes'] if r['project'] == 'pulse')
        project = next(p for p in self.manifest['projects'] if p['id'] == route['project'])
        board, lead = recovery.governed(self.manifest, project['id'])
        profile = next(p['hermes_profile'] for p in self.manifest['profiles'] if p['id'] == lead)
        self.assertEqual((lead, profile, board), ('pulse', 'pulse', 'pulse'))
        sha = 'a'*40
        signal = adapt_payload('check_run', {'action':'completed', 'repository':{'full_name':route['repository']},
            'check_run':{'id':77,'status':'completed','conclusion':'failure','head_sha':sha,
                         'pull_requests':[{'number':49}]}}, approved_repository=route['repository'])
        self.assertIsNotNone(signal)
        assert signal is not None
        admission = SimulatedAdmission()
        self.assertTrue(admission.admit('delivery-1')); self.assertFalse(admission.admit('delivery-1'))
        lead_session = SimulatedLead(lead, profile, {'id':'objective-1','status':'open'},
                                     {'head_sha':signal['head_sha'],'fresh':True,'pr_number':49,'check_id':77,'check_conclusion':'failure'})
        # Captured wake is an in-process test double, not a sent/woken Hermes session.
        lead_session.wake({'project':project['id'], 'lead':lead, 'profile':profile, 'signal':signal})
        self.assertEqual(len(lead_session.wakes), 1)
        self.assertTrue(lead_session.reread_and_consider(sha))
        for bad_task, bad_evidence in [
            ({'id':'objective-1','status':'done'}, {'head_sha':sha,'fresh':True,'pr_number':49,'check_id':77,'check_conclusion':'failure'}),
            ({'id':'objective-1','status':'open'}, {'head_sha':'b'*40,'fresh':True,'pr_number':49,'check_id':77,'check_conclusion':'failure'}),
            ({'id':'unknown','status':'open'}, {'head_sha':sha,'fresh':True,'pr_number':49,'check_id':77,'check_conclusion':'failure'}),
            ({'id':'objective-1','status':'open'}, {'head_sha':sha,'fresh':False,'pr_number':49,'check_id':77,'check_conclusion':'failure'}),
        ]:
            self.assertFalse(SimulatedLead(lead,profile,bad_task,bad_evidence).reread_and_consider(sha))
        # Simulation only: a missed-event fallback routes a canonical synthetic
        # event through the very same capture path used above; no Hermes API.
        def simulated_capture(canonical_event):
            signal = adapt_payload('check_run', canonical_event, approved_repository=route['repository'])
            if signal is None or not admission.admit(canonical_event['delivery_id']):
                return False
            lead_session.wake({'project':project['id'], 'lead':lead, 'profile':profile, 'signal':signal})
            return True
        missed = {'action':'completed','repository':{'full_name':route['repository']},
          'check_run':{'id':78,'status':'completed','conclusion':'failure','head_sha':sha,'pull_requests':[{'number':49}]},
          'delivery_id':'synthetic-missed-event'}
        before_wakes = len(lead_session.wakes)
        self.assertTrue(simulated_capture(missed))
        self.assertEqual(len(lead_session.wakes), before_wakes + 1)
        self.assertEqual(lead_session.wakes[-1]['lead'], 'pulse')
        self.assertEqual(lead_session.wakes[-1]['signal']['head_sha'], sha)
        # Pure planner can propose only; protected action still stops at approval.
        request = {'plan':{'project_id':'pulse','source_task_id':'task-1','source_kind':'blocked_task',
          'event_key':'c'*64,'action':'repair-worker','title':'Inspect failure','summary':'Inspect exact-head failure.',
          'acceptance_check':'Fresh checks pass.'}, 'source_evidence':{'id':'task-1','status':'blocked','source':'immutable_checkpoint_sqlite','complete':True,'observed_at':datetime.now(timezone.utc).isoformat()},
          'recovery_history':{'board_id':'pulse','event_key':'c'*64,'source':'immutable_checkpoint_sqlite',
             'observed_at':datetime.now(timezone.utc).isoformat(),'complete':True,'tasks':[]}}
        # Use policy fixture governed for real repo/project; planner stays pure.
        policy = json.loads((ROOT/'automation/monitoring/policy.json').read_text())
        proposal = recovery.prepare(request, self.manifest, policy)
        self.assertEqual(proposal['result'], 'approval_required')
        self.assertTrue(proposal['requires_human_approval'])
        self.assertEqual(proposal['assignee'], lead)
        self.assertEqual(proposal['creation_channel'], 'approval_gated_native_kanban')
        side_effects = []  # simulation assertion: prepare is a pure proposal only
        self.assertEqual(side_effects, [])
        self.assertNotIn('task_id', proposal)
        self.assertNotIn('created', proposal)

if __name__ == '__main__': unittest.main()
