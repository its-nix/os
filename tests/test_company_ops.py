import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('health', ROOT / 'skills/company-ops/scripts/health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
NOW = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)
SCRIPTS = ROOT / 'skills/company-ops/scripts'


class DigestTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name)
        self.manifest = {'id': 'demo', 'board': 'demo', 'lifecycle': 'operating',
                         'integrations': {'crm': {'enabled': False}}, 'outcome_metrics': {'revenue': 0}}
        self.write('ventures/demo/manifest.json', self.manifest)

    def write(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def run_digest(self, now=NOW, *args, **env):
        defaults = dict(os.environ, HERMES_HOME=str(self.home), HERMES_BIN='/missing-hermes',
                        TWENTY_API_KEY='unused-fixture-key')
        return subprocess.run([sys.executable, str(SCRIPTS / 'digest.py'), '--at', now.isoformat(), *args],
                              env={**defaults, **env}, capture_output=True, text=True)

    def test_scorecard_tracks_previous_value_and_distinguishes_zero_from_unknown(self):
        first = self.run_digest()
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn('| Revenue | 0 | unavailable | unavailable |', first.stdout)
        self.assertIn('| Paying customers | unavailable | unavailable | unavailable |', first.stdout)
        self.assertIn('| Blocked cards | unavailable | unavailable | unavailable |', first.stdout)
        self.assertLess(first.stdout.index('| Revenue'), first.stdout.index('| Blocked cards'))
        self.assertIn('CRM disabled', first.stdout)
        self.manifest['outcome_metrics']['revenue'] = 12
        self.write('ventures/demo/manifest.json', self.manifest)
        second = self.run_digest(NOW + timedelta(days=1), '--preview')
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertIn('| Revenue | 12 | 0 | +12 |', second.stdout)
        self.assertEqual(second.stdout, self.run_digest(NOW + timedelta(days=1), '--preview').stdout)

    def test_oldest_revenue_decision_first_with_receipt_timings(self):
        for action, hours, revenue, extra in (
            ('routine', 48, False, {}), ('recent-sale', 2, True, {}),
            ('old-sale', 30, True, {}), ('sent', 6, True, {
                'status': 'closed', 'outcome': 'executed', 'recorded_at': '2026-11-24T06:00:00Z',
                'lead_received_at': '2026-11-24T01:00:00Z', 'draft_ready_at': '2026-11-24T01:30:00Z',
                'founder_sent_at': '2026-11-24T03:00:00Z'}),
        ):
            self.write(f'ventures/demo/actions/{action}.json', {
                'action_id': action, 'status': 'awaiting-founder', 'requested_at': (NOW - timedelta(hours=hours)).isoformat(),
                'revenue_blocking': revenue, 'preview': 'private fixture preview', **extra})
        text = self.run_digest().stdout
        self.assertLess(text.index('old-sale'), text.index('recent-sale'))
        self.assertLess(text.index('recent-sale'), text.index('routine'))
        self.assertIn('30 h', text)
        self.assertIn('| Executed approvals | 1 |', text)
        self.assertIn('| Lead received → draft ready (min) | 30 |', text)
        self.assertIn('| Draft ready → founder sent (min) | 90 |', text)
        self.assertNotIn('private fixture preview', text)

    def test_existing_cron_migrates_without_recreating_or_rescheduling(self):
        self.write('cron/jobs.json', {'jobs': [{'id': 'existing', 'name': 'nix-founder-digest', 'no_agent': False},
                                               {'name': 'nix-company-health'}, {'name': 'nix-improvement'}]})
        spec = importlib.util.spec_from_file_location('setup_loops', SCRIPTS / 'setup_loops.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'HERMES_BIN': 'hermes'}), \
                patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(0, setup.main(['--reconcile']))
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(['hermes', '-p', 'default', 'cron', 'edit', 'existing'], command[:6])
        self.assertIn('--no-agent', command)
        self.assertNotIn('--schedule', command)

    def test_native_board_health_and_optional_github_counts(self):
        self.manifest['integrations']['github_repo'] = 'fixture/sample'
        self.write('ventures/demo/manifest.json', self.manifest)
        self.write('ventures/other/manifest.json', {'id': 'other', 'board': 'other', 'lifecycle': 'retired'})
        self.write('nix/health.json', {'backup_last_success': (NOW - timedelta(days=2)).isoformat(),
                                      'deployed_at': (NOW - timedelta(hours=1)).isoformat(),
                                      'deployed_revision': 'a' * 40, 'deploy_attempt_status': 'failed'})
        cli = self.home / 'hermes'
        board = [{'status': 'blocked'}, {'status': 'done'}, {'status': 'in_progress',
                 'started_at': (NOW - timedelta(hours=25)).timestamp()}]
        cli.write_text(f'#!/bin/sh\ncase "$5" in\n demo) printf "%s\\n" \'{json.dumps(board)}\';;\n'
                       ' other) printf "[]\\n";;\n *) exit 2;;\nesac\n')
        gh = self.home / 'gh'
        gh.write_text('#!/bin/sh\nprintf \'[{"number":2,"mergedAt":"2026-11-24T00:00:00Z"}]\\n\'\n')
        for executable in (cli, gh):
            executable.chmod(0o755)
        result = self.run_digest(HERMES_BIN=str(cli), PATH=f'{self.home}{os.pathsep}{os.environ["PATH"]}')
        self.assertEqual(0, result.returncode, result.stderr)
        for line in ('| Blocked cards | 1 |', '| Stalled cards | 1 |', '| Done cards | 1 |',
                     '| Blocked cards | 0 |', '| Health incidents | 2 |', '| Merged PRs | 1 |',
                     '## other · retired', 'Last verified deployed revision · aaaaaaaaaaaa'):
            self.assertIn(line, result.stdout)


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


if __name__ == '__main__':
    unittest.main()
