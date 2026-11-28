from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

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
        recent = (NOW - timedelta(minutes=5)).timestamp()
        quiet = self.run_check(kanban=lambda b: [{'id': 't_0', 'status': 'blocked', 'title': 'new', 'created_at': old,
                                                  'blocked_since': recent}])
        self.assertFalse(any('t_0' in p for p in quiet))  # old card, blocked just now: no alert
        problems = self.run_check(kanban=lambda b: [{'id': 't_1', 'status': 'blocked', 'title': 'waiting', 'blocked_since': old}])
        self.assertTrue(any('not delivered' in p for p in problems))
        self.assertTrue(any('t_1 blocked >24h' in p for p in problems))

    def test_unreadable_board_and_bad_timestamps_do_not_hide_other_findings(self):
        (self.home / 'nix/health.json').write_text(json.dumps({'backup_last_success': 'garbage', 'deployed_at': None}))
        venture = self.home / 'ventures/acme'
        venture.mkdir(parents=True)
        (venture / 'manifest.json').write_text(json.dumps({'id': 'acme', 'lifecycle': 'operating', 'board': 'acme'}))
        def broken(board):
            raise RuntimeError('timeout')
        problems = '\n'.join(self.run_check(kanban=broken, t3_ok=False))
        for needle in ('backup stale', 'No healthy deployment', 'board acme unreadable', 'T3 Code is unreachable'):
            self.assertIn(needle, problems)

    def test_paused_ventures_are_ignored(self):
        self.status()
        venture = self.home / 'ventures/old'
        venture.mkdir(parents=True)
        (venture / 'manifest.json').write_text(json.dumps({'id': 'old', 'lifecycle': 'paused', 'board': 'old'}))
        self.assertEqual([], self.run_check(kanban=lambda b: self.fail('paused boards are not read')))

    def write(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def host_status(self, **updates):
        self.status()
        path = self.home / 'nix/health.json'
        self.write('nix/health.json', dict(json.loads(path.read_text()), **updates))

    def test_container_health_and_each_missing_auth_are_reported(self):
        self.host_status(containers={'hermes': 'healthy', 'reviewer': 'running', 't3': 'exited',
                                     'twenty-server': 'disabled'},
                         t3_provider_auth={'codex': True, **dict.fromkeys(
                             ('claude', 'opencode', 'grok', 'antigravity'), False)})
        self.assertCountEqual(
            ['Container t3 degraded (exited)'] +
            [f'T3 provider {p}: missing auth state' for p in ('claude', 'opencode', 'grok', 'antigravity')],
            self.run_check(),
        )

    def test_reviewer_alerts_only_when_the_configured_pool_is_exhausted(self):
        future = (NOW + timedelta(hours=1)).isoformat()
        past = (NOW - timedelta(minutes=1)).isoformat()
        self.host_status(reviewer_pool=['claude', 'codex'], reviewer_cooldowns={'claude': future, 'codex': past})
        self.assertEqual([], self.run_check())
        self.host_status(reviewer_pool=['claude'], reviewer_cooldowns={'claude': future})
        self.assertEqual([f'Reviewer cooldowns exhausted the pool: claude until {future}'], self.run_check())

    def test_healthy_freshness_and_pat_reminders(self):
        self.host_status(observed_at=NOW.isoformat(), approval_worker_last_success=NOW.isoformat(),
                         pack_sync={'generation': 'a' * 64, 'synced_at': NOW.isoformat()},
                         builder_pat_expires_on='2026-12-20')
        self.assertEqual([], self.run_check())
        self.host_status(observed_at=(NOW - timedelta(hours=3)).isoformat(), approval_worker_last_success=None,
                         pack_sync=None, builder_pat_expires_on='2026-11-30')
        self.assertEqual(['Host health snapshot stale or unavailable',
                          'Approval worker stale: last success unknown',
                          'NixKit pack sync stale or unavailable',
                          'Builder PAT expires on 2026-11-30'], self.run_check())

    def test_cron_failure_streak_on_active_profiles(self):
        self.status()
        self.write('ventures/acme/manifest.json', {'id': 'acme', 'lifecycle': 'operating',
                                                   'board': 'acme', 'lead_profile': 'lead'})
        self.write('ventures/old/manifest.json', {'id': 'old', 'lifecycle': 'paused', 'board': 'old'})
        self.write('cron/jobs.json', {'jobs': [{'name': 'paused', 'enabled': False, 'failure_streak': 9},
                                             {'name': 'healthy', 'failure_streak': 0}]})
        self.write('profiles/old/cron/jobs.json', [{'name': 'historical', 'failure_streak': 1}])
        self.write('profiles/lead/cron/jobs.json', [{'name': 'venture-cron', 'failure_streak': 2}])
        self.assertEqual(['lead: cron venture-cron failing (streak 2)'], self.run_check())

    def test_stuck_card_is_measured_from_latest_claim(self):
        self.status()
        (self.home / 'kanban.db').touch()
        old = (NOW - timedelta(hours=25)).timestamp()
        boundary = (NOW - timedelta(hours=24)).timestamp()
        tasks = [{'id': 't_recent', 'status': 'running', 'created_at': old, 'started_at': old},
                 {'id': 't_stuck', 'status': 'running', 'started_at': old}]
        details = [{'task': task, 'events': [{'kind': 'claimed', 'created_at': old}]} for task in tasks]
        details[0]['events'].append({'kind': 'claimed', 'created_at': boundary})
        outputs = [type('Result', (), {'returncode': 0, 'stdout': json.dumps(data)})
                   for data in (tasks, *details)]
        with patch.object(health.subprocess, 'run', side_effect=outputs):
            self.assertEqual(['default: card t_stuck in_progress >24h'],
                             self.run_check(kanban=health.board_tasks))

    def test_usage_spikes_require_a_positive_baseline(self):
        self.status()
        audit = self.home / 'cron/usage_audit.jsonl'
        audit.parent.mkdir()
        current = (NOW - timedelta(days=1)).isoformat()
        baseline = (NOW - timedelta(days=8)).isoformat()
        latest = json.dumps({'ts': current, 'total_tokens': 250_000})
        audit.write_text(latest + '\n')
        self.assertEqual([], self.run_check())
        audit.write_text(latest + '\nmalformed\n' + json.dumps({'ts': baseline, 'total_tokens': 100_000}) + '\n')
        self.assertEqual(['Weekly token spike: 250,000 vs 100,000 in the previous week'], self.run_check())

        with sqlite3.connect(self.home / 'state.db') as db:
            db.execute('CREATE TABLE session_model_usage (first_seen REAL, last_seen REAL, '
                       'actual_cost_usd REAL, estimated_cost_usd REAL)')
            before = (NOW - timedelta(days=8)).timestamp()
            after = (NOW - timedelta(days=1)).timestamp()
            db.executemany('INSERT INTO session_model_usage VALUES (?, ?, ?, ?)',
                           [(before, before, 3, 30), (after, after, 0, 9), (before, after, 100, 100)])
        self.assertEqual(['Weekly token spike: 250,000 vs 100,000 in the previous week',
                          'Weekly cost spike: $9.00 vs $3.00 in the previous week'], self.run_check())


if __name__ == '__main__':
    unittest.main()
