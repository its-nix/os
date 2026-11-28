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
            if obj == 'noteTargets':
                assert set(body) <= {'noteId', 'targetPersonId', 'targetOpportunityId'}, body  # v2.62 schema
            row = dict(body, id=f'{obj}-{len(self.rows[obj]) + 1}')
            self.rows[obj].append(row)
            return {'data': {'create': row}}
        if method == 'PATCH':
            rid = path.split('/')[3]
            row = next(r for r in self.rows[obj] if r['id'] == rid)
            row.update(body)
            return {'data': {'update': row}}
        if 'filter=' not in path:
            return {'data': {obj: self.rows[obj]}}
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
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        patcher = patch.dict(os.environ, {'HERMES_HOME': self.td.name})
        patcher.start()
        self.addCleanup(patcher.stop)
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
        self.assertEqual(['Lead intake: owner@bakery.example', 'Qualified MEETING: owner@bakery.example'], titles)
        self.assertEqual(4, len(self.fake.rows['noteTargets']))

    def test_intake_heals_a_missing_note_after_a_crash(self):
        self.crm.create('people', {'name': {'firstName': 'Dana', 'lastName': ''}, 'emails': {'primaryEmail': 'd@x.example'}})
        self.crm.create('opportunities', {'name': 'Dana', 'stage': 'NEW', 'pointOfContactId': 'people-1'})
        self.run_cli('intake', '--email', 'd@x.example', '--first', 'Dana')
        self.run_cli('intake', '--email', 'd@x.example', '--first', 'Dana')
        self.assertEqual(1, len(self.fake.rows['notes']))

    def test_note_links_are_repaired_on_retry(self):
        self.run_cli('intake', '--email', 'r@x.example', '--first', 'R')
        self.fake.rows['noteTargets'].clear()  # crash after note creation, before links
        self.run_cli('intake', '--email', 'r@x.example', '--first', 'R')
        self.assertEqual(1, len(self.fake.rows['notes']))
        self.assertEqual(2, len(self.fake.rows['noteTargets']))

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
        self.m = load('approval')
        self.m.OUTBOX = Path(self.td.name) / 'outbox'
        self.m.INBOX = Path(self.td.name) / 'status'
        self.m.INBOX.mkdir()
        drafts = Path(self.td.name) / 'ventures/demo/drafts'
        drafts.mkdir(parents=True)
        self.preview = drafts / 'demo-dana-1.md'
        self.preview.write_text('Hi Dana, could we talk for 15 minutes about restocking?')

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def args(self, **kw):
        base = dict(venture='demo', action_id='demo-dana-1', target='email owner@bakery.example',
                    effect='Send one email', preview='demo-dana-1.md', cost='none', expires='2026-12-01',
                    executor='founder')
        base.update(kw)
        return types.SimpleNamespace(**base)

    def deliver(self, receipt, status='pending'):
        (self.m.INBOX / (receipt['status_key'] + '.json')).write_text(json.dumps({
            'action_id': receipt['action_id'], 'venture': receipt['venture'],
            'status': status, 'todoist_task': 'example-task',
        }))

    def test_inactive_venture_refuses_new_requests_and_retries(self):
        previous = self.m.request(self.args())
        manifest = self.preview.parent.parent / 'manifest.json'
        self.m.OUTBOX.mkdir()
        for lifecycle in ('paused', 'retired'):
            manifest.write_text(json.dumps({'lifecycle': lifecycle}))
            with self.subTest(lifecycle=lifecycle):
                with self.assertRaisesRegex(SystemExit, 'not accepting'):
                    self.m.request(self.args(action_id='another-action'))
                with self.assertRaisesRegex(SystemExit, 'not accepting'):
                    self.m.request(self.args())
                self.assertEqual(previous, self.m.status(self.args()))
                self.assertEqual([], list(self.m.OUTBOX.iterdir()))
                self.assertFalse(self.m.receipt_path('demo', 'another-action').exists())

    def test_request_never_executes_and_is_idempotent(self):
        self.m.OUTBOX.mkdir()
        receipt = self.m.request(self.args())
        self.assertEqual('queued-local', receipt['status'])
        self.assertIsNone(receipt['outcome'])
        [spooled] = self.m.OUTBOX.glob('*.json')
        body = json.loads(spooled.read_text())
        self.assertEqual(self.preview.read_text(), body['preview'])
        self.assertEqual(set(self.m.REQUEST_FIELDS), set(body))
        # A local retry must keep the original action and exact preview, even if the draft changed.
        self.preview.write_text('A different draft')
        retry = self.m.request(self.args(effect='Different effect'))
        self.assertEqual(receipt['status_key'], retry['status_key'])
        self.assertEqual(body, json.loads(spooled.read_text()))
        self.deliver(receipt, 'completed-by-founder')
        self.assertEqual('example-task', self.m.request(self.args())['todoist_task'])
        completed = self.m.status(self.args())
        self.assertEqual('awaiting-founder', completed['status'])
        self.assertEqual('completed-by-founder', completed['queue_status'])
        self.assertIsNone(completed['outcome'])

    def test_without_queue_mount_it_stays_local_and_can_resume(self):
        with patch.dict(os.environ, {'TODOIST_API_TOKEN': 'unused-test-value'}):
            receipt = self.m.request(self.args())
        self.assertEqual('queued-local', receipt['status'])
        self.assertFalse(self.m.OUTBOX.exists())
        self.m.OUTBOX.mkdir()
        self.m.request(self.args())
        self.assertTrue((self.m.OUTBOX / (receipt['status_key'] + '.json')).is_file())

    def test_undelivered_request_can_only_expire(self):
        self.m.request(self.args())  # queued-local: the founder never saw it
        for outcome in ('executed', 'declined'):
            with self.subTest(outcome=outcome), self.assertRaises(SystemExit):
                self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome=outcome, evidence='x'))
        self.assertEqual('closed', self.m.record(types.SimpleNamespace(
            venture='demo', action_id='demo-dana-1', outcome='expired', evidence='withdrawn by lead'))['status'])

    def test_fifo_and_symlink_previews_refused(self):
        drafts = Path(self.td.name) / 'ventures/demo/drafts'
        os.mkfifo(drafts / 'pipe.md')
        (drafts / 'link.md').symlink_to(self.preview)
        for name in ('pipe.md', 'link.md'):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.m.request(self.args(preview=name))

    def test_outcome_needs_evidence_and_is_recorded_once(self):
        self.deliver(self.m.request(self.args()))
        with self.assertRaises(SystemExit):
            self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='executed', evidence=' '))
        done = self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='declined',
                                                   evidence='founder reply 2026-11-23'))
        self.assertEqual('closed', done['status'])
        with self.assertRaises(SystemExit):
            self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-1', outcome='executed', evidence='x'))

    def test_delivered_action_outcome_recordable_after_queue_closes_or_expires(self):
        for n, queue in enumerate(('closed', 'expired')):
            with self.subTest(queue=queue):
                action = f'demo-dana-q{n}'
                self.deliver(self.m.request(self.args(action_id=action)), status=queue)
                done = self.m.record(types.SimpleNamespace(venture='demo', action_id=action, outcome='executed',
                                                           evidence='founder executed, receipt link'))
                self.assertEqual('executed', done['outcome'])
        undelivered = self.m.request(self.args(action_id='demo-dana-u'))
        with self.assertRaises(SystemExit):  # never delivered: can only expire
            self.m.record(types.SimpleNamespace(venture='demo', action_id='demo-dana-u', outcome='executed', evidence='x'))

    def test_unsafe_previews_and_ids_refused(self):
        outside = Path(self.td.name) / 'secret.txt'
        outside.write_text('private')
        for preview in ('../../secret.txt', str(outside)):
            with self.subTest(preview=preview), self.assertRaises(SystemExit):
                self.m.request(self.args(preview=preview))
        self.preview.write_text('x' * (self.m.MAX_PREVIEW + 1))
        with self.assertRaisesRegex(SystemExit, 'exceeds'):
            self.m.request(self.args())
        self.preview.write_text('  ')
        with self.assertRaises(SystemExit):
            self.m.request(self.args())
        with self.assertRaises(SystemExit):
            self.m.request(self.args(action_id='../../etc'))


if __name__ == '__main__':
    unittest.main()
