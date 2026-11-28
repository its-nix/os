import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'skills/venture-factory/scripts/venture.py'

# Stateful fake of the Hermes 0.23.0 CLI surface used by the scaffolder (formats observed live).
FAKE = r'''#!/usr/bin/env python3
import json, os, sqlite3, sys, uuid
from pathlib import Path
home = Path(os.environ['HERMES_HOME']); a = sys.argv[1:]
log = home / 'calls.log'; log.parent.mkdir(parents=True, exist_ok=True)
with log.open('a') as f: f.write(json.dumps(a) + '\n')
profile = None
if a[:1] == ['-p']: profile, a = a[1], a[2:]
pdir = home / 'profiles' / profile if profile else home
boards = home / 'boards.json'
def load(p, d): return json.loads(p.read_text()) if p.exists() else d
if a[:2] == ['profile', 'create']:
    d = home / 'profiles' / a[2]
    if d.exists(): print('exists', file=sys.stderr); sys.exit(1)
    d.mkdir(parents=True); (d / 'SOUL.md').write_text('stock soul'); sys.exit(0)
if a == ['config', 'get', 'kanban', '--json']:
    print(json.dumps(load(home / 'kanban-config.json', {
        'max_in_progress': 2, 'max_in_progress_per_profile': 1, 'default_assignee': '',
    }))); sys.exit(0)
root = home / 'kanban' / 'boards'
if a[:3] == ['kanban', 'boards', 'list']:
    entries = load(boards, [{'slug': 'default', 'archived': False}])
    entries += [json.loads(p.read_text()) for p in root.glob('*/board.json')]
    print(json.dumps([b for b in entries if '--all' in a or not b['archived']])); sys.exit(0)
if a[:3] == ['kanban', 'boards', 'create']:
    board = root / a[3]
    board.mkdir(parents=True)
    (board / 'board.json').write_text(json.dumps({'slug': a[3], 'archived': False}))
    with sqlite3.connect(board / 'kanban.db') as db:
        db.executescript("""
            CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT,
                               priority INTEGER DEFAULT 0, max_runtime_seconds INTEGER, max_retries INTEGER,
                               key TEXT UNIQUE);
            CREATE TABLE task_events(task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
            CREATE TABLE task_links(parent_id TEXT, child_id TEXT);
        """)
    sys.exit(0)
if a[:2] == ['kanban', '--board']:
    board = root / a[2]
    with sqlite3.connect((board / 'kanban.db').as_uri() + '?mode=rw', uri=True) as db:
        db.row_factory = sqlite3.Row
        if a[3] == 'list':
            print(json.dumps([dict(t) for t in db.execute('SELECT * FROM tasks WHERE status=?', (a[5],))]))
        elif a[3] == 'reclaim':
            db.execute("UPDATE tasks SET status='ready' WHERE id=? AND status='running'", (a[4],))
        elif a[3] == 'create':
            key = a[a.index('--idempotency-key') + 1]
            db.execute("INSERT OR IGNORE INTO tasks(id,title,body,status,key) VALUES (?,?,?,'ready',?)",
                       ('t_' + uuid.uuid4().hex[:8], a[4], a[a.index('--body')+1], key))
            print(json.dumps(dict(db.execute('SELECT * FROM tasks WHERE key=?', (key,)).fetchone())))
        else:
            sys.exit(2)
    sys.exit(0)
if a[:1] == ['cron']:
    store = pdir / 'cron' / 'jobs.json'; store.parent.mkdir(parents=True, exist_ok=True)
    data = load(store, {'jobs': []})
    if a[1] == 'create':
        paused = '--paused' in a
        data['jobs'].append({'id': uuid.uuid4().hex[:12], 'name': a[a.index('--name') + 1], 'enabled': True,
                             'state': 'paused' if paused else 'scheduled', 'paused_at': 'x' if paused else None,
                             'skills': [a[i + 1] for i, v in enumerate(a) if v == '--skill'],
                             'schedule': {'expr': a[2]}, 'prompt': a[3], 'catch_up': False,
                             'timezone': a[a.index('--timezone')+1] if '--timezone' in a else None})
    elif a[1] == 'edit':
        j = next(j for j in data['jobs'] if j['id'] == a[2])
        j.update(schedule={'expr': a[a.index('--schedule')+1]}, prompt=a[a.index('--prompt')+1],
                 skills=[a[a.index('--skill')+1]], catch_up=False, timezone=a[a.index('--timezone')+1] or None)
    elif a[1] in ('pause', 'resume'):
        for j in data['jobs']:
            if j['id'] == a[2]:
                j['enabled'] = a[1] == 'resume'
                j['state'] = 'paused' if a[1] == 'pause' else 'scheduled'
                j['paused_at'] = 'x' if a[1] == 'pause' else None
    store.write_text(json.dumps(data)); sys.exit(0)
print('unexpected', a, file=sys.stderr); sys.exit(2)
'''


class VentureFactoryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name) / 'home'
        self.home.mkdir()
        fake = Path(self.td.name) / 'hermes'
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = patch_env({'HERMES_BIN': str(fake)})
        self.env.__enter__()
        spec = importlib.util.spec_from_file_location('venture', TOOL)
        self.v = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.v)

    def tearDown(self):
        self.env.__exit__(None, None, None)
        self.td.cleanup()

    def call(self, *argv):
        return self.v.main(['--home', str(self.home), *argv])

    def create(self, vid='acme', *extra):
        prd, gtm = Path(self.td.name) / 'PRD.md', Path(self.td.name) / 'GTM.md'
        prd.write_text('# PRD\nMetric: 5 paying shops in 30 days.')
        gtm.write_text('# GTM\nChannel: direct visits.')
        return self.call('create', '--id', vid, '--name', 'Acme Widgets', '--mission',
                         'Validate that 5 shops will pay for widget restocking', '--approval-ref',
                         'founder decision 2026-11-23', '--prd', str(prd), '--gtm', str(gtm), *extra)

    def calls(self):
        log = self.home / 'calls.log'
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def jobs(self, vid='acme'):
        return json.loads((self.home / f'profiles/{vid}/cron/jobs.json').read_text())['jobs']

    def manifest(self, vid='acme'):
        return json.loads((self.home / f'ventures/{vid}/manifest.json').read_text())

    def test_create_scaffolds_lead_board_loops_and_mission_generically(self):
        self.assertEqual(0, self.create())
        m = self.manifest()
        self.assertEqual(('incubating', 'acme', 'acme'), (m['lifecycle'], m['lead_profile'], m['board']))
        self.assertIn('core', m['packs'])
        self.assertEqual('venture-acme', m['memory']['bank'])
        self.assertFalse(m['integrations']['crm']['enabled'])
        soul = (self.home / 'profiles/acme/SOUL.md').read_text()
        self.assertIn('Acme Widgets', soul)
        self.assertIn('Founder-gated', soul)
        self.assertTrue((self.home / 'ventures/acme/CHARTER.md').is_file())
        self.assertEqual({'acme-outcome', 'acme-distill'}, {j['name'] for j in self.jobs()})
        self.assertTrue(all(j['state'] == 'scheduled' and j['skills'] == ['venture-lead'] for j in self.jobs()))
        self.assertTrue(m['first_task'].startswith('t_'))

    def test_create_is_idempotent(self):
        self.assertEqual(0, self.create())
        first = self.manifest()
        self.assertEqual(0, self.create())
        second = self.manifest()
        self.assertEqual(first['first_task'], second['first_task'])
        self.assertEqual(2, len(self.jobs()))
        self.assertEqual(1, sum(c[:2] == ['profile', 'create'] for c in self.calls()))
        self.assertEqual(1, sum(c[:3] == ['kanban', 'boards', 'create'] for c in self.calls()))
        self.assertEqual(1, len(second['history']))

    def test_foreign_or_legacy_profile_is_never_adopted(self):
        (self.home / 'profiles/legacy').mkdir(parents=True)
        (self.home / 'profiles/legacy/SOUL.md').write_text('old venture memory')
        self.assertEqual(1, self.create('legacy'))
        self.assertEqual('old venture memory', (self.home / 'profiles/legacy/SOUL.md').read_text())
        self.assertFalse((self.home / 'ventures/legacy/manifest.json').exists())
        self.assertFalse(any(c[:2] == ['-p', 'legacy'] for c in self.calls()))

    def test_reserved_invalid_and_unapproved_ids_refused(self):
        for vid in ('default', 'personal', 'Bad', '1x', 'a', 'has-dash'):
            with self.subTest(vid=vid):
                self.assertEqual(1, self.create(vid))
        prd = Path(self.td.name) / 'p.md'
        prd.write_text('x')
        self.assertEqual(1, self.call('create', '--id', 'acme', '--name', 'A', '--mission', 'm',
                                      '--approval-ref', '  ', '--prd', str(prd), '--gtm', str(prd)))
        self.assertFalse((self.home / 'profiles/acme').exists())

    def test_pause_resume_retire_control_routines_and_keep_history(self):
        self.create()
        self.assertEqual(0, self.call('stage', '--id', 'acme', '--to', 'operating'))
        self.assertEqual(0, self.call('pause', '--id', 'acme', '--reason', 'founder hold'))
        self.assertTrue(all(j['state'] == 'paused' for j in self.jobs()))
        self.assertEqual(0, self.call('resume', '--id', 'acme'))
        self.assertEqual('operating', self.manifest()['lifecycle'])
        self.assertTrue(all(j['state'] == 'scheduled' for j in self.jobs()))
        self.assertEqual(0, self.call('retire', '--id', 'acme', '--reason', 'no traction'))
        self.assertTrue(all(j['state'] == 'paused' for j in self.jobs()))
        self.assertEqual(1, self.call('resume', '--id', 'acme'))
        self.assertEqual(1, self.create())  # retired ventures are not re-created
        self.assertTrue((self.home / 'profiles/acme').is_dir())
        self.assertEqual(['incubating', 'operating', 'paused', 'operating', 'retired'],
                         [h['to'] for h in self.manifest()['history']])

    def board_db(self):
        return self.home / 'kanban/boards/acme/kanban.db'

    def test_board_hold_reclaims_running_work_and_preserves_native_ids(self):
        self.assertEqual(0, self.create())
        first = self.manifest()['first_task']
        with sqlite3.connect(self.board_db()) as db:
            db.execute("UPDATE tasks SET status='running', assignee='acme'")
            db.execute("INSERT INTO tasks(id,status,assignee) VALUES ('second','ready','acme')")
        self.assertEqual(0, self.call('pause', '--id', 'acme'))
        self.assertNotIn('acme', self.v.Hermes(self.home).boards())
        self.assertFalse(self.board_db().exists())
        self.assertEqual(1, self.call('release', '--id', 'acme', first))
        held_db = self.home / self.manifest()['held_board'] / 'kanban.db'
        with sqlite3.connect(held_db) as db:
            self.assertEqual([(first, 'ready'), ('second', 'ready')],
                             db.execute('SELECT id,status FROM tasks ORDER BY rowid').fetchall())
        for _ in range(2):
            self.assertEqual(0, self.call('resume', '--id', 'acme'))
        with sqlite3.connect(self.board_db()) as db:
            self.assertEqual([(first,), ('second',)], db.execute('SELECT id FROM tasks ORDER BY rowid').fetchall())
        self.assertFalse(held_db.exists())

    def test_retire_parks_gateway_and_strips_only_venture_credentials(self):
        self.assertEqual(0, self.create())
        profile = self.home / 'profiles/acme'
        env = profile / '.env'
        env.write_text('# keep me\nMCP_T3_API_KEY=fixture\n export TWENTY_API_KEY = fixture\nOTHER=keep\n')
        (profile / 'memory.md').write_text('retained')
        for _ in range(2):
            self.assertEqual(0, self.call('retire', '--id', 'acme'))
        self.assertEqual('# keep me\nOTHER=keep\n', env.read_text())
        self.assertTrue((profile / 'gateway.parked').is_file())
        self.assertEqual('retained', (profile / 'memory.md').read_text())
        self.assertFalse(self.board_db().exists())

    def test_release_prioritizes_constraint_keeps_dependencies_and_reserves_work(self):
        self.assertEqual(0, self.create())
        first = self.manifest()['first_task']
        with sqlite3.connect(self.board_db()) as db:
            db.execute("INSERT INTO tasks(id,status) VALUES ('dependent','todo'),('sibling','ready'),('extra','ready')")
            db.execute('INSERT INTO task_links VALUES (?,?)', (first, 'dependent'))
        self.assertEqual(0, self.call('release', '--id', 'acme', 'dependent', 'sibling', 'extra'))
        with sqlite3.connect(self.board_db()) as db:
            self.assertEqual([(first, 'ready'), ('dependent', 'todo'), ('sibling', 'ready')], db.execute(
                "SELECT id,status FROM tasks WHERE assignee='acme' AND max_runtime_seconds=300 "
                "AND max_retries=2 ORDER BY priority DESC").fetchall())
            self.assertEqual(30, sum(json.loads(row[0])['minutes'] for row in db.execute(
                "SELECT payload FROM task_events WHERE kind='venture_released'")))
            db.execute("UPDATE tasks SET status='done' WHERE assignee='acme'")
        self.assertEqual(0, self.call('release', '--id', 'acme', 'extra'))
        with sqlite3.connect(self.board_db()) as db:
            self.assertIsNone(db.execute("SELECT assignee FROM tasks WHERE id='extra'").fetchone()[0])

    def test_work_minutes_can_limit_releases_before_card_count(self):
        self.assertEqual(0, self.create())
        manifest = self.manifest()
        manifest['budgets']['max_work_minutes_per_window'] = 10
        self.v.Registry(self.home).save(manifest)
        first = manifest['first_task']
        with sqlite3.connect(self.board_db()) as db:
            db.execute("INSERT INTO tasks(id,status) VALUES ('extra','ready')")
        self.assertEqual(0, self.call('release', '--id', 'acme', first, 'extra'))
        with sqlite3.connect(self.board_db()) as db:
            self.assertEqual([(first,)], db.execute("SELECT id FROM tasks WHERE assignee='acme'").fetchall())

    def test_release_refuses_unsafe_dispatch_settings(self):
        self.assertEqual(0, self.create())
        first = self.manifest()['first_task']
        (self.home / 'kanban-config.json').write_text(json.dumps({
            'max_in_progress': 2, 'max_in_progress_per_profile': 1, 'default_assignee': 'default',
        }))
        self.assertEqual(1, self.call('release', '--id', 'acme', first))
        with sqlite3.connect(self.board_db()) as db:
            self.assertIsNone(db.execute('SELECT assignee FROM tasks WHERE id=?', (first,)).fetchone()[0])

    def test_resume_restores_enabled_custom_cron_jobs_only(self):
        self.assertEqual(0, self.create())
        store = self.home / 'profiles/acme/cron/jobs.json'
        custom = [{'id': 'enabled', 'enabled': True}, {'id': 'disabled', 'enabled': False, 'state': 'paused'}]
        store.write_text(json.dumps({'jobs': self.jobs() + custom}))
        self.assertEqual(0, self.call('pause', '--id', 'acme'))
        self.assertTrue(all(not job['enabled'] for job in self.jobs()))
        self.assertEqual(0, self.call('resume', '--id', 'acme'))
        self.assertEqual([True, False], [job['enabled'] for job in self.jobs()[-2:]])

    def test_reconcile_edits_drift_in_place_with_idempotent_receipts(self):
        self.assertEqual(0, self.create('acme', '--timezone', 'UTC'))
        existing = self.jobs()
        store = self.home / 'profiles/acme/cron/jobs.json'
        drifted = self.jobs()
        drifted[0].update(prompt='one card', schedule={'expr': '0 1 * * *'}, timezone='Etc/GMT', catch_up=True)
        store.write_text(json.dumps({'jobs': drifted}))
        self.assertEqual(0, self.call('reconcile', '--id', 'acme'))
        self.assertEqual(existing, self.jobs())
        receipts = self.manifest()['loop_reconciliation']
        self.assertEqual(existing[0]['id'], receipts[existing[0]['name']]['job_id'])
        self.assertIn('up to 3', existing[0]['prompt'])
        self.assertIn('Current constraint', existing[0]['prompt'])
        before = self.calls()
        self.assertEqual(0, self.call('reconcile', '--id', 'acme'))
        self.assertEqual(before, self.calls())
        self.assertEqual(receipts, self.manifest()['loop_reconciliation'])

    def test_locally_modified_soul_is_preserved(self):
        self.create()
        (self.home / 'profiles/acme/SOUL.md').write_text('lead refined its own contract')
        self.create()
        self.assertEqual('lead refined its own contract', (self.home / 'profiles/acme/SOUL.md').read_text())
        self.assertIn('soul', self.manifest()['drift'])

    def test_dry_run_changes_nothing(self):
        self.assertEqual(0, self.create('acme', '--dry-run'))
        self.assertFalse((self.home / 'profiles/acme').exists())
        self.assertFalse((self.home / 'nix').exists())
        self.assertFalse((self.home / 'calls.log').exists())  # no Hermes command runs at all

    def test_crash_after_profile_create_is_adopted_but_symlinks_are_not(self):
        # Simulate: intent recorded, `profile create` ran, process died before the marker write.
        self.create()
        (self.home / 'profiles/acme' / self.v.MARKER).unlink()
        self.assertEqual(0, self.create())
        self.assertTrue((self.home / 'profiles/acme' / self.v.MARKER).is_file())
        # A marker reached through a symlinked profile is never trusted.
        real = self.home / 'elsewhere'
        (self.home / 'profiles/acme').rename(real)
        (self.home / 'profiles/acme').symlink_to(real)
        self.assertEqual(1, self.call('pause', '--id', 'acme'))

    def test_failed_activation_leaves_routines_paused_and_retry_completes(self):
        import unittest.mock as mock
        real_run = self.v.Hermes.run
        def failing(h, *argv, **kw):
            if argv[:2] == ('cron', 'resume'):
                raise self.v.Refusal('injected')
            return real_run(h, *argv, **kw)
        with mock.patch.object(self.v.Hermes, 'run', failing):
            self.assertEqual(1, self.create())
        self.assertTrue(all(j['state'] == 'paused' for j in self.jobs()))  # never half-active
        self.assertEqual(['acme-outcome', 'acme-distill'], self.manifest()['loops'])  # pause can find them
        self.assertEqual(0, self.create())
        self.assertTrue(all(j['state'] == 'scheduled' for j in self.jobs()))
        self.assertEqual(1, sum(c[:3] == ['kanban', '--board', 'acme'] for c in self.calls()))

    def test_failed_retire_is_retried_to_completion(self):
        import unittest.mock as mock
        self.create()
        real_run = self.v.Hermes.run
        calls = {'n': 0}
        def flaky(h, *argv, **kw):
            if argv[:2] == ('cron', 'pause'):
                calls['n'] += 1
                if calls['n'] == 2:
                    raise self.v.Refusal('injected')
            return real_run(h, *argv, **kw)
        with mock.patch.object(self.v.Hermes, 'run', flaky):
            self.assertEqual(1, self.call('retire', '--id', 'acme'))
        self.assertEqual('retired', self.manifest()['lifecycle'])
        self.assertEqual(0, self.call('retire', '--id', 'acme'))  # retry reconciles
        self.assertTrue(all(j['state'] == 'paused' for j in self.jobs()))

    def test_create_rerun_never_pauses_an_active_venture(self):
        self.create()
        self.create()
        self.assertFalse(any(c[:3] == ['-p', 'acme', 'cron'] and c[3] == 'pause' for c in self.calls()))

    def test_preexisting_board_is_not_reused(self):
        (self.home / 'boards.json').write_text(json.dumps([{'slug': 'default', 'archived': False},
                                                           {'slug': 'acme', 'archived': False}]))
        self.assertEqual(1, self.create())
        self.assertFalse((self.home / 'profiles/acme/cron').exists())

    def test_provisioning_requires_a_nonempty_decision_package(self):
        empty = Path(self.td.name) / 'empty.md'
        empty.write_text('  ')
        prd = Path(self.td.name) / 'prd.md'
        prd.write_text('# PRD')
        missing = str(Path(self.td.name) / 'missing.md')
        for prd_arg, gtm_arg in ((str(prd), str(empty)), (str(prd), missing), (str(empty), str(prd)), (missing, str(prd))):
            with self.subTest(prd=prd_arg, gtm=gtm_arg):
                self.assertEqual(1, self.call('create', '--id', 'acme', '--name', 'A', '--mission', 'm',
                                              '--approval-ref', 'ok', '--prd', prd_arg, '--gtm', gtm_arg))
        self.assertEqual([], [p.name for p in self.home.iterdir()])  # no manifest, lock or Hermes call

    def test_rerun_uses_registry_copies_and_blank_registry_copy_is_refused(self):
        self.assertEqual(0, self.create())
        self.assertEqual(0, self.call('create', '--id', 'acme', '--name', 'Acme Widgets', '--mission',
                                      'Validate that 5 shops will pay for widget restocking',
                                      '--approval-ref', 'founder decision 2026-11-23'))
        (self.home / 'ventures/acme/GTM.md').write_text('   ')
        self.assertEqual(1, self.call('create', '--id', 'acme', '--name', 'Acme Widgets', '--mission',
                                      'Validate that 5 shops will pay for widget restocking',
                                      '--approval-ref', 'founder decision 2026-11-23'))

    def test_no_venture_names_hardcoded_in_generic_code(self):
        text = (TOOL.read_text() + (ROOT / 'skills/venture-factory/SKILL.md').read_text()).lower()
        for legacy in ('pulse', 'algua', 'meabeclick', 'nixvert'):
            self.assertNotIn(legacy, text)


class patch_env:
    def __init__(self, values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == '__main__':
    unittest.main()
