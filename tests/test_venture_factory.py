import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'skills/venture-factory/scripts/venture.py'

# Stateful fake of the Hermes 0.23.0 CLI surface used by the scaffolder (formats observed live).
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, uuid
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
if a[:3] == ['kanban', 'boards', 'list']:
    print(json.dumps(load(boards, [{'slug': 'default', 'archived': False}]))); sys.exit(0)
if a[:3] == ['kanban', 'boards', 'create']:
    b = load(boards, [{'slug': 'default', 'archived': False}]); b.append({'slug': a[3], 'archived': False})
    boards.write_text(json.dumps(b)); sys.exit(0)
if a[:2] == ['kanban', '--board'] and a[3] == 'create':
    t = load(home / 'tasks.json', {}); key = a[a.index('--idempotency-key') + 1]
    t.setdefault(key, {'id': 't_' + uuid.uuid4().hex[:8], 'title': a[4], 'board': a[2],
                       'assignee': a[a.index('--assignee') + 1]})
    (home / 'tasks.json').write_text(json.dumps(t)); print(json.dumps(t[key])); sys.exit(0)
if a[:1] == ['cron']:
    store = pdir / 'cron' / 'jobs.json'; store.parent.mkdir(parents=True, exist_ok=True)
    data = load(store, {'jobs': []})
    if '--script' in a and not (pdir / 'scripts' / a[a.index('--script') + 1]).is_file():  # real cron refuses these
        print('Script file not found', file=sys.stderr); sys.exit(1)
    if a[1] == 'create':
        paused = '--paused' in a
        data['jobs'].append({'id': uuid.uuid4().hex[:12], 'name': a[a.index('--name') + 1], 'enabled': True,
                             'state': 'paused' if paused else 'scheduled', 'paused_at': 'x' if paused else None,
                             'skills': [a[i + 1] for i, v in enumerate(a) if v == '--skill'],
                             'script': a[a.index('--script') + 1] if '--script' in a else None})
    elif a[1] == 'edit':
        for j in data['jobs']:
            if j['id'] == a[2]:
                j['script'] = a[a.index('--script') + 1]
    elif a[1] in ('pause', 'resume'):
        for j in data['jobs']:
            if j['id'] == a[2]:
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

    def test_loops_carry_an_idle_gate_and_the_distillation_rule_and_existing_jobs_get_the_gate(self):
        self.assertEqual(0, self.create())
        self.assertEqual({'acme-outcome': 'nix-os/gate-outcome.py', 'acme-distill': 'nix-os/gate-distill.py'},
                         {j['name']: j['script'] for j in self.jobs()})
        self.assertFalse(any(c[:2] == ['-p', 'acme'] and c[2:4] == ['cron', 'edit'] for c in self.calls()))
        collector = (ROOT / 'skills/company-ops/scripts/collect_evidence.py').read_text()
        for name in ('gate-outcome.py', 'gate-distill.py'):  # installed before the jobs that name them
            gate = self.home / 'profiles/acme/scripts/nix-os' / name
            self.assertEqual((collector, 0o755), (gate.read_text(), gate.stat().st_mode & 0o777))
        distill = next(c for c in self.calls() if 'acme-distill' in c)
        prompt = distill[distill.index('create') + 2]
        for needle in ('Deterministic-first', 'script, CI check, lint or API constraint', 'Never auto-apply',
                       'never edit the tests or metric'):
            self.assertIn(needle, prompt)
        store = self.home / 'profiles/acme/cron/jobs.json'
        data = json.loads(store.read_text())
        for job in data['jobs']:  # jobs created by an earlier release have no gate
            job['script'] = None
        store.write_text(json.dumps(data))
        self.assertEqual(0, self.create())  # rerun reconciles exactly the script field
        edits = [c for c in self.calls() if c[2:4] == ['cron', 'edit']]
        self.assertEqual(2, len(edits))
        self.assertTrue(all(c[5] == '--script' and len(c) == 7 for c in edits))
        self.assertEqual(['nix-os/gate-outcome.py', 'nix-os/gate-distill.py'], sorted(j['script'] for j in self.jobs())[::-1])
        self.assertEqual(0, self.create())
        self.assertEqual(2, len([c for c in self.calls() if c[2:4] == ['cron', 'edit']]))  # no further edits

    def test_loops_are_created_ungated_when_the_collector_or_a_safe_target_is_missing(self):
        self.v.GATE_SOURCE = self.home / 'missing-collector.py'
        self.assertEqual(0, self.create('beta'))
        self.assertEqual([None, None], [j['script'] for j in self.jobs('beta')])
        self.assertFalse(any('--script' in c for c in self.calls()))
        self.assertFalse((self.home / 'profiles/beta/scripts').exists())

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
