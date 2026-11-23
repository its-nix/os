import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'skills/crm-lead-ops/scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTwenty:
    """Minimal REST semantics of Twenty's generated API for people/opportunities/notes."""

    def __init__(self):
        self.rows = {'people': [], 'opportunities': [], 'notes': [], 'noteTargets': []}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        obj = path.split('/')[2].split('?')[0]
        if method == 'POST':
            row = dict(body, id=f'{obj}-{len(self.rows[obj]) + 1}')
            self.rows[obj].append(row)
            return {'data': {'create': row}}
        if method == 'PATCH':
            rid = path.split('/')[3]
            row = next(r for r in self.rows[obj] if r['id'] == rid)
            row.update(body)
            return {'data': {'update': row}}
        flt = path.split('filter=', 1)[1].split('&')[0]
        from urllib.parse import unquote
        field, value = unquote(flt).split('[eq]:')
        value = value.strip('"')
        def get(row):
            if field == 'emails.primaryEmail':
                return row.get('emails', {}).get('primaryEmail')
            return row.get(field)
        return {'data': {obj: [r for r in self.rows[obj] if get(r) == value]}}


class CrmTests(unittest.TestCase):
    def setUp(self):
        self.crm_mod = load('crm')
        self.fake = FakeTwenty()
        self.crm = self.crm_mod.Twenty(key='k', opener=self.fake)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            self.crm_mod.main(list(argv), crm=self.crm)
        return json.loads(out.getvalue())

    def test_intake_is_idempotent_and_qualification_logs_evidence(self):
        a = self.run_cli('intake', '--email', 'owner@bakery.example', '--first', 'Dana', '--company', 'Crumb', '--source', 'referral')
        b = self.run_cli('intake', '--email', 'owner@bakery.example', '--first', 'Dana')
        self.assertTrue(a['created'])
        self.assertFalse(b['created'])
        self.assertEqual(a['opportunity'], b['opportunity'])
        self.assertEqual(1, len(self.fake.rows['people']))
        self.assertEqual(1, len(self.fake.rows['opportunities']))
        q = self.run_cli('qualify', '--email', 'owner@bakery.example', '--stage', 'MEETING', '--rationale', 'Confirmed weekly manual restock pain')
        self.assertEqual('MEETING', self.fake.rows['opportunities'][0]['stage'])
        self.assertEqual('MEETING', q['stage'])
        titles = [n['title'] for n in self.fake.rows['notes']]
        self.assertEqual(['Lead intake', 'Qualified: MEETING'], titles)

    def test_qualify_requires_existing_record(self):
        with self.assertRaises(SystemExit):
            self.run_cli('qualify', '--email', 'nobody@x.example', '--stage', 'LOST', '--rationale', 'r')

    def test_missing_key_is_a_clear_blocker(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SystemExit, 'TWENTY_API_KEY'):
                self.crm_mod.Twenty()


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.td.name}, clear=False)
        self.env.start()
        os.environ.pop('TODOIST_API_TOKEN', None)
        self.m = load('approval')
        self.preview = Path(self.td.name) / 'draft.md'
        self.preview.write_text('Hi Dana, could we talk for 15 minutes about restocking?')

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def args(self, **kw):
        base = dict(venture='demo', action_id='demo-dana-1', target='email owner@bakery.example',
                    effect='Send one email', preview=str(self.preview), cost='none', expires='2026-12-01',
                    executor='founder')
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_request_never_executes_and_is_idempotent(self):
        posted = []
        receipt = self.m.request(self.args(), post=lambda body: posted.append(body) or 'task-1')
        self.assertEqual('awaiting-founder', receipt['status'])
        self.assertIsNone(receipt['outcome'])
        self.assertIn('does not authorize', posted[0]['description'])
        again = self.m.request(self.args(), post=lambda body: posted.append(body) or 'task-2')
        self.assertEqual('task-1', again['todoist_task'])
        self.assertEqual(1, len(posted))  # never queued twice

    def test_without_queue_credential_it_stays_local_and_blocked(self):
        receipt = self.m.request(self.args())
        self.assertEqual('queued-local', receipt['status'])

    def test_outcome_needs_evidence_and_is_recorded_once(self):
        self.m.request(self.args())
        with self.assertRaises(SystemExit):
            self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='executed', evidence=' '))
        done = self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='declined',
                                                   evidence='founder reply 2026-11-23'))
        self.assertEqual('closed', done['status'])
        with self.assertRaises(SystemExit):
            self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='executed', evidence='x'))

    def test_empty_preview_and_unsafe_ids_refused(self):
        self.preview.write_text('  ')
        with self.assertRaises(SystemExit):
            self.m.request(self.args())
        with self.assertRaises(SystemExit):
            self.m.request(self.args(action_id='../../etc'))


if __name__ == '__main__':
    unittest.main()
