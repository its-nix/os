from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load('approval_worker', 'scripts/approval/worker.py')
NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


class FakeTodoist(worker.Todoist):
    def __init__(self):
        super().__init__('unused-test-value', 'Project1')
        self.active = {}
        self.completed = {}
        self.commands = {}
        self.calls = []
        self.offline = False
        self.lose_create_reply = False
        self.lose_close_reply = False

    def call(self, path, params=None, data=None, *, post=False):
        self.calls.append((path, params and dict(params), post))
        if self.offline:
            raise TimeoutError
        if path == 'projects/Project1':
            return {'id': self.project, 'name': 'Nix', 'is_archived': False, 'is_shared': False}
        if path == 'tasks':
            return {'results': list(self.active.values()), 'next_cursor': None}
        if path == 'tasks/completed/by_completion_date':
            return {'items': list(self.completed.values()), 'next_cursor': None}
        if path == 'sync':
            [command] = json.loads(data['commands'])
            assert data['resource_types'] == '[]'
            assert command['type'] == 'item_add'
            key = command['uuid']
            if key not in self.commands:
                task_id = f'Task{len(self.commands) + 1}'
                self.commands[key] = task_id
                self.active[task_id] = dict(command['args'], id=task_id)
            if self.lose_create_reply:
                self.lose_create_reply = False
                raise TimeoutError
            return {'sync_status': {key: 'ok'}, 'temp_id_mapping': {command['temp_id']: self.commands[key]}}
        if path.startswith('tasks/') and path.endswith('/close'):
            self.complete(path.split('/')[1])
            if self.lose_close_reply:
                self.lose_close_reply = False
                raise TimeoutError
            return None
        raise AssertionError('Unexpected network endpoint')

    def complete(self, task_id):
        self.completed[task_id] = self.active.pop(task_id)


class FounderApprovalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.spool = self.root / 'approvals'
        for name in ('outbox', 'status', 'state'):
            (self.spool / name).mkdir(parents=True)
        self.remote = FakeTodoist()
        self.key = 'a' * 32

    def request(self, key=None, **overrides):
        key = key or self.key
        body = dict(venture='demo', action_id='demo-action-1', target='A protected test marker',
                    effect='Create the marker', preview='Verbatim\n[link](https://example.invalid) **literal**',
                    cost='none', expires='2026-11-26', executor='founder', status_key=key)
        body.update(overrides)
        (self.spool / 'outbox' / (key + '.json')).write_text(json.dumps(body))
        return body

    def status(self, key=None):
        return json.loads((self.spool / 'status' / ((key or self.key) + '.json')).read_text())

    def tick(self, minutes=0):
        worker.tick(self.root, self.remote, NOW + timedelta(minutes=minutes))

    def test_schema_rejects_extra_fields_duplicate_keys_links_as_markup_and_special_files(self):
        for i, fields in enumerate((
                {'project_id': 'OtherProject'}, {'action_id': '../bad'}, {'target': 'fake\nheader'},
                {'preview': '```\nForged approval'}, {'effect': '\u202eFake effect'}, {'cost': []},
                {'preview': 'x' * 3001}, {'expires': '2026-02-30'}, {'expires': '2027-04-01'})):
            with self.subTest(fields=tuple(fields)):
                key = f'{i:032x}'
                self.request(key, **fields)
                self.tick()
                self.assertEqual({'status': 'closed', 'reason': 'invalid-request'}, self.status(key))
        path = self.spool / 'outbox' / (self.key + '.json')
        for make in (lambda: path.symlink_to('/dev/null'), lambda: os.mkfifo(path),
                     lambda: path.write_text('{"venture":"demo","venture":"override"}'),
                     lambda: path.write_bytes(b' ' * (worker.MAX_REQUEST_BYTES + 1))):
            make()
            self.tick()
            self.assertEqual('invalid-request', self.status()['reason'])
        self.assertEqual([], self.remote.calls)

    def test_idempotent_by_action_id_and_safe_preview(self):
        body = self.request(effect='Create [marker](https://example.invalid)')
        self.tick()
        task = self.remote.active['Task1']
        self.assertEqual('Project1', task['project_id'])
        self.assertEqual(['nix-approval'], task['labels'])
        self.assertEqual('[approval] demo: Create marker https example invalid', task['content'])
        self.assertTrue(task['description'].startswith('Action id: demo-action-1\n'))
        self.assertIn(body['preview'] + '\n```', task['description'])
        self.assertEqual(2, task['description'].count('```'))
        self.assertIn('does not authorize', task['description'].split('```')[0])
        self.assertEqual({'status': 'pending', 'action_id': 'demo-action-1',
                          'venture': 'demo', 'todoist_task': 'Task1'}, self.status())
        self.request(effect='Altered on retry')
        other_key = 'b' * 32
        self.request(other_key, venture='another-venture')
        self.tick(2)
        self.assertEqual('action-id-already-used', self.status(other_key)['reason'])
        self.assertNotIn('todoist_task', self.status(other_key))
        self.assertEqual(1, len(self.remote.commands))

    def test_unreachable_and_lost_create_reply_reconcile_without_duplicates(self):
        self.request()
        self.remote.offline = True
        with self.assertRaises(TimeoutError):
            self.tick()
        self.assertNotIn('todoist_task', self.status())
        self.remote.offline = False
        self.remote.lose_create_reply = True
        with self.assertRaises(TimeoutError):
            self.tick(2)
        self.assertNotIn('todoist_task', self.status())
        self.tick(4)
        self.assertEqual('Task1', self.status()['todoist_task'])
        self.assertEqual(1, sum(path == 'sync' for path, _, _ in self.remote.calls))

    def test_restore_reconciles_a_completed_task_before_create(self):
        self.request()
        self.tick()
        self.remote.complete('Task1')
        (self.spool / 'state/queue.sqlite3').unlink()  # Restore from before the task mapping.
        self.request()
        self.tick(2)
        self.assertEqual('completed-by-founder', self.status()['status'])
        self.assertEqual(1, sum(path == 'sync' for path, _, _ in self.remote.calls))

    def test_completion_only_publishes_status(self):
        self.request()
        self.tick()
        self.remote.complete('Task1')
        self.remote.calls.clear()
        self.tick(2)
        self.assertEqual('completed-by-founder', self.status()['status'])
        self.assertNotIn('outcome', self.status())
        self.assertFalse(any(post for _, _, post in self.remote.calls))
        self.assertFalse((self.root / 'protected-marker').exists())

    def test_expiry_closes_once_and_expired_unsent_is_never_created(self):
        self.request()
        self.tick()
        self.remote.lose_close_reply = True
        with self.assertRaises(TimeoutError):
            self.tick(24 * 60)
        self.tick(24 * 60 + 2)
        self.assertEqual('expired', self.status()['status'])
        self.assertIn('Task1', self.remote.completed)
        expired_key = 'c' * 32
        self.request(expired_key, action_id='demo-expired', expires='2026-11-25')
        self.tick(24 * 60 + 4)
        self.assertEqual('expired', self.status(expired_key)['status'])
        self.assertEqual(1, len(self.remote.commands))
        self.assertEqual(1, sum(path.endswith('/close') for path, _, _ in self.remote.calls))

    def test_expiry_does_not_close_a_task_with_founder_added_subtasks(self):
        self.request()
        self.tick()
        self.remote.active['child'] = {'id': 'child', 'parent_id': 'Task1', 'project_id': 'Project1',
                                       'description': 'Founder follow-up'}
        self.tick(24 * 60)
        self.assertEqual('closed', self.status()['status'])
        self.assertIn('Task1', self.remote.active)
        self.assertIn('child', self.remote.active)

    def test_new_tasks_per_tick_are_bounded(self):
        for i in range(worker.MAX_POLLS + 2):
            self.request(f'{i:032x}', action_id=f'demo-rate-{i}')
        self.tick()
        self.assertEqual(worker.MAX_POLLS, len(self.remote.commands))
        self.tick(2)
        self.assertEqual(worker.MAX_POLLS + 2, len(self.remote.commands))

    def test_todoist_reads_only_the_configured_project_and_follows_cursors(self):
        pages = [
            {'results': [{'id': 'Ignored', 'project_id': 'OtherProject'}], 'next_cursor': 'page2'},
            {'results': [{'id': 'Task1', 'project_id': 'Project1'}], 'next_cursor': None},
        ]
        seen = []

        def page(path, params):
            seen.append((path, dict(params)))
            return pages.pop(0)

        with patch.object(self.remote, 'call', side_effect=page):
            self.assertEqual(['Task1'], [task['id'] for task in self.remote.tasks()])
        self.assertTrue(all(params['project_id'] == 'Project1' for _, params in seen))
        self.assertEqual('page2', seen[1][1]['cursor'])

    def test_no_compose_service_mounts_the_token_or_private_state(self):
        compose = ROOT / 'deploy/compose.yaml'
        sources = re.findall(r'^\s+source: \$\{NIX_CLEAN_ROOT[^}]*\}([^\s#]*)', compose.read_text(), re.M)
        self.assertIn('/approvals/outbox', sources)
        self.assertIn('/approvals/status', sources)
        for source in sources:
            for private in ('/todoist/token', '/approvals/state/queue.sqlite3'):
                self.assertFalse(Path(private).is_relative_to(Path(source or '/')), source)
        if not shutil.which('docker'):
            return
        probe = subprocess.run(['docker', 'compose', 'version'], capture_output=True)
        if probe.returncode:
            return
        (self.root / 'twenty').mkdir()
        for path in ('hindsight.env', 'twenty/db.env', 'twenty/server.env'):
            (self.root / path).touch()
        config = json.loads(subprocess.check_output(
            ['docker', 'compose', '-f', str(compose), '--profile', 'crm', 'config', '--format', 'json'],
            env=dict(os.environ, NIX_CLEAN_ROOT=str(self.root), HERMES_IMAGE='nixos-hermes:test', T3_BUILD_TAG='test', TODOIST_API_TOKEN='test-fixture')))
        spool_mounts = []
        for name, service in config['services'].items():
            self.assertNotIn('TODOIST_API_TOKEN', service.get('environment', {}))
            for mount in service.get('volumes', []):
                source = Path(mount['source'])
                for private in ('todoist/token', 'approvals/state/queue.sqlite3'):
                    self.assertFalse((self.root / private).is_relative_to(source))
                if source.parent == self.spool:
                    spool_mounts.append((name, source.name, mount.get('read_only', False)))
        self.assertCountEqual([('hermes', 'outbox', False), ('hermes', 'status', True)], spool_mounts)


if __name__ == '__main__':
    unittest.main()
