"""Synthetic-only continuity contract simulation; never live delivery proof."""
import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from automation.monitoring import recovery
from automation.monitoring.github_webhook import adapt_payload

ROOT = Path(__file__).resolve().parents[1]


class SimulatedAdmission:
    """Test double, not Hermes durable native delivery-ID admission."""
    def __init__(self): self.ids = set()
    def admit(self, delivery_id):
        if delivery_id in self.ids: return False
        self.ids.add(delivery_id); return True


class InMemoryFixtureStore:
    """Synthetic test fixtures only; not native Kanban/GitHub canonical state."""
    def __init__(self, task, evidence):
        self.task, self.evidence = task, evidence
        self.task_reads = self.evidence_reads = 0
    def read_task(self, task_id):
        self.task_reads += 1
        return json.loads(json.dumps(self.task)) if self.task.get('id') == task_id else None
    def read_evidence(self):
        self.evidence_reads += 1
        return json.loads(json.dumps(self.evidence))


class SimulatedLead:
    """Test-only Lead wake capture and fixture reread, not a Hermes session."""
    def __init__(self, lead, profile, store, objective_contract):
        self.lead, self.profile, self.store = lead, profile, store
        self.objective_contract = objective_contract
        self.wakes, self.planner_calls = [], 0
    def wake(self, payload): self.wakes.append(payload)
    def reread(self, task_id, expected):
        task = self.store.read_task(task_id); evidence = self.store.read_evidence()
        keys = ('project', 'repo', 'pr_number', 'check_id', 'head_sha', 'fresh')
        objective = self.objective_contract
        return bool(task and task_id == objective['task_id'] and expected['task_id'] == objective['task_id']
                    and expected['project'] == objective['project_id']
                    and task.get('id') == objective['task_id'] and task.get('project') == objective['project_id']
                    and task.get('status') in ('open','blocked')
                    and evidence.get('task_id') == objective['task_id']
                    and evidence.get('project') == objective['project_id']
                    and all(evidence.get(k) == expected[k] for k in keys)
                    and re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', evidence.get('head_sha', ''))
                    and evidence.get('fresh') is True)
    def plan_after_reread(self, task_id, expected, request, manifest, policy):
        if not self.reread(task_id, expected): return None
        self.planner_calls += 1
        return recovery.prepare(request, manifest, policy)


class LeadContinuityIntegrationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT/'nix-os.yaml').read_text())
        self.routes = json.loads((ROOT/'automation/monitoring/github-webhook-routes.json').read_text())

    def test_simulated_replay_fallback_wake_reread_and_approval_boundary(self):
        route = next(r for r in self.routes['routes'] if r['project'] == 'pulse')
        project = next(p for p in self.manifest['projects'] if p['id'] == route['project'])
        board, lead = recovery.governed(self.manifest, project['id'])
        profile = next(p['hermes_profile'] for p in self.manifest['profiles'] if p['id'] == lead)
        self.assertEqual((lead, profile, board), ('pulse', 'pulse', 'pulse'))
        sha = 'a'*40
        admission = SimulatedAdmission()
        objective_contract = MappingProxyType({'task_id':'objective-1', 'project_id':'pulse'})
        store = InMemoryFixtureStore({'id':'objective-1','project':'pulse','status':'open'}, {
            'task_id':'objective-1', 'project':'pulse', 'repo':route['repository'], 'pr_number':49,
            'check_id':77, 'head_sha':sha, 'fresh':True, 'check_name':'CI', 'check_conclusion':'failure'})
        session = SimulatedLead(lead, profile, store, objective_contract)
        expected = {'task_id':'objective-1','project':project['id'],'repo':route['repository'],'pr_number':49,
                    'check_id':77,'head_sha':sha,'fresh':True}

        def pipeline(delivery_id, payload):
            if not admission.admit(delivery_id): return False
            signal = adapt_payload('check_run', payload, approved_repository=route['repository'])
            if signal is None: return False
            session.wake({'project':project['id'], 'lead':lead, 'profile':profile, 'signal':signal})
            return True

        initial = {'action':'completed','repository':{'full_name':route['repository']},
          'check_run':{'id':77,'status':'completed','conclusion':'failure','head_sha':sha,'pull_requests':[{'number':49}]}}
        self.assertTrue(pipeline('delivery-1', initial))
        self.assertFalse(pipeline('delivery-1', initial))
        self.assertEqual(len(session.wakes), 1)
        self.assertEqual(session.wakes[0]['lead'], lead)
        self.assertTrue(session.reread('objective-1', expected))
        self.assertEqual((store.task_reads, store.evidence_reads), (1,1))

        # Test-only fallback scans a synthetic snapshot for an outstanding event.
        # No production fallback function exists; this is not runtime proof.
        def test_only_fallback(snapshot):
            for event in snapshot:
                if event.get('outstanding') and pipeline(event['delivery_id'], event['payload']):
                    return True
            return False
        missed = {'action':'completed','repository':{'full_name':route['repository']},
          'check_run':{'id':78,'status':'completed','conclusion':'failure','head_sha':sha,'pull_requests':[{'number':49}]}}
        snapshot = [{'outstanding':True,'delivery_id':'synthetic-missed-event','payload':missed}]
        self.assertTrue(test_only_fallback(snapshot))
        self.assertEqual(len(session.wakes), 2)
        self.assertEqual(session.wakes[-1]['lead'], lead)
        self.assertFalse(test_only_fallback(snapshot))
        self.assertEqual(len(session.wakes), 2)

        policy = json.loads((ROOT/'automation/monitoring/policy.json').read_text())
        now = datetime.now(timezone.utc).isoformat()
        request = {'plan':{'project_id':'pulse','source_kind':'repo_check','repo':route['repository'],
          'pr_number':49,'check_name':'CI','event_key':'c'*64,'action':'deploy','title':'Deploy verified fix',
          'summary':'Review exact-head check evidence before deployment.', 'acceptance_check':'Human approves deployment.'},
          'source_evidence':{'complete':True,'source':'github_checks_read','observed_at':now,'repo':route['repository'],
            'repo':route['repository'],'pr_number':49,'state':'OPEN', 'checks':[{'name':'CI','conclusion':'FAILURE'}]},
          'recovery_history':{'board_id':'pulse','event_key':'c'*64,'source':'immutable_checkpoint_sqlite','observed_at':now,'complete':True,'tasks':[]}}
        proposal = session.plan_after_reread('objective-1', expected, request, self.manifest, policy)
        self.assertEqual(proposal['result'], 'approval_required')
        self.assertTrue(proposal['requires_human_approval'])
        self.assertEqual(proposal['assignee'], lead)
        self.assertNotIn('task_id', proposal)
        self.assertNotIn('created', proposal)
        self.assertEqual(session.planner_calls, 1)

        # Mutate only the synthetic in-memory fixture store; expected event stays fixed.
        original_task = dict(store.task)
        original_evidence = dict(store.evidence)
        negative_cases = [
            ('wrong task ID', 'task', 'id', 'objective-other'),
            ('wrong project', 'task', 'project', 'other'),
            ('wrong evidence project', 'evidence', 'project', 'other'),
            ('wrong repository', 'evidence', 'repo', 'other/repo'),
            ('wrong PR', 'evidence', 'pr_number', 50),
            ('wrong check', 'evidence', 'check_id', 999),
            ('stale evidence', 'evidence', 'fresh', False),
            ('valid but wrong head', 'evidence', 'head_sha', 'b'*40),
            ('malformed non-hex SHA', 'evidence', 'head_sha', 'g'*40),
        ]
        for label, target, key, value in negative_cases:
            with self.subTest(case=label):
                store.task.clear(); store.task.update(original_task)
                store.evidence.clear(); store.evidence.update(original_evidence)
                (store.task if target == 'task' else store.evidence)[key] = value
                before = session.planner_calls
                self.assertIsNone(session.plan_after_reread('objective-1', expected, request, self.manifest, policy))
                self.assertEqual(session.planner_calls, before)
        store.task.clear(); store.task.update(original_task)
        store.evidence.clear(); store.evidence.update(original_evidence)
        # Both mutable records agree with each other, but conflict with the fixed objective.
        store.task['project'] = 'algua'
        store.evidence['project'] = 'algua'
        before = session.planner_calls
        self.assertIsNone(session.plan_after_reread('objective-1', expected, request, self.manifest, policy))
        self.assertEqual(session.planner_calls, before)

if __name__ == '__main__': unittest.main()
