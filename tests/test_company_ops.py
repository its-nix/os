from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('health', ROOT / 'skills/company-ops/scripts/health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
NOW = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        (self.home / 'nix').mkdir()

    def tearDown(self):
        self.td.cleanup()

    def status(self, backup_hours=4, deploy_hours=3, attempt='healthy'):
        (self.home / 'nix/health.json').write_text(json.dumps({
            'backup_last_success': (NOW - timedelta(hours=backup_hours)).isoformat(),
            'backup_last_attempt_status': 'published',
            'deployed_at': (NOW - timedelta(hours=deploy_hours)).isoformat(), 'deploy_attempt_status': attempt}))

    def run_check(self, **kw):
        kw.setdefault('kanban', lambda board: [])
        kw.setdefault('t3_ok', True)
        kw.setdefault('disk', 0.5)
        return health.check(self.home, NOW, **kw)

    def test_healthy_company_is_silent(self):
        self.status()
        self.assertEqual([], self.run_check())

    def test_stale_backup_deploy_failure_disk_and_t3_are_reported(self):
        self.status(backup_hours=40, attempt='failed')
        problems = self.run_check(t3_ok=False, disk=0.95)
        text = '\n'.join(problems)
        for needle in ('backup stale', 'deployment attempt failed', 'disk 95%', 'T3 Code is unreachable'):
            self.assertIn(needle, text)

    def test_venture_blockers_and_undelivered_approvals(self):
        self.status()
        venture = self.home / 'ventures/acme'
        (venture / 'actions').mkdir(parents=True)
        (venture / 'manifest.json').write_text(json.dumps({'id': 'acme', 'lifecycle': 'operating', 'board': 'acme'}))
        (venture / 'actions/acme-x-1.json').write_text(json.dumps({'action_id': 'acme-x-1', 'status': 'queued-local'}))
        old = (NOW - timedelta(hours=30)).timestamp()
        problems = self.run_check(kanban=lambda b: [{'id': 't_1', 'status': 'blocked', 'title': 'waiting', 'created_at': old}])
        self.assertTrue(any('not delivered' in p for p in problems))
        self.assertTrue(any('t_1 blocked >24h' in p for p in problems))

    def test_paused_ventures_are_ignored(self):
        self.status()
        venture = self.home / 'ventures/old'
        venture.mkdir(parents=True)
        (venture / 'manifest.json').write_text(json.dumps({'id': 'old', 'lifecycle': 'paused', 'board': 'old'}))
        self.assertEqual([], self.run_check(kanban=lambda b: self.fail('paused boards are not read')))


if __name__ == '__main__':
    unittest.main()
