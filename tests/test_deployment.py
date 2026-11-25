import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment', ROOT / 'scripts/deploy-os.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
deployment.T3_UID, deployment.T3_GID = os.getuid(), os.getgid()
deployment.ROOT_UID, deployment.ROOT_GID = os.getuid(), os.getgid()
deployment.HERMES_UID, deployment.HERMES_GID = os.getuid(), os.getgid()  # Tests run unprivileged.
deployment.HINDSIGHT_UID, deployment.HINDSIGHT_GID = os.getuid(), os.getgid()


_real_apply = deployment.apply


def _real_venture_profiles(state):
    return deployment._token_module().venture_profiles(state / 'hermes')


def apply(*args, **kwargs):
    kwargs.setdefault('t3_credential', lambda state: False)
    kwargs.setdefault('t3_loaded', lambda state: None)
    kwargs.setdefault('units', lambda source, run: False)
    kwargs.setdefault('memory', lambda state, run: state / '.hindsight-plugin-reload')  # see test_hindsight_memory
    kwargs.setdefault('login', lambda source: None)
    kwargs.setdefault('memory_preflight', lambda source, state, image, run: None)
    kwargs.setdefault('redaction', lambda source, image, run: None)  # see test_hindsight_memory
    kwargs.setdefault('personal', lambda source, state, run: 'present')
    return _real_apply(*args, **kwargs)


class DeploymentTests(unittest.TestCase):
    def prepare_state(self, td):
        state = Path(td) / 'state'
        (state / 'hermes/nix').mkdir(parents=True)
        (state / 'hermes/profiles').mkdir()
        (state / 'knowledge/company').mkdir(parents=True)
        (state / 'paperclip').mkdir()
        (state / 'hermes/memory.md').write_text('Learning must survive failed deployment')
        (state / 'hermes/nix/versions.json').write_text((ROOT / 'deploy/versions.json').read_text())
        (state / 'installed-inputs.json').write_text(json.dumps(deployment.native_inputs(ROOT)))
        return state

    def healthy_runner(self, calls):
        images = json.loads((ROOT / 'deploy/versions.json').read_text())

        def healthy(argv):
            calls.append(argv)
            if argv[:3] == ['docker', 'image', 'ls']:
                return 'sha256:present'
            if argv[:2] == ['docker', 'inspect']:
                name = argv[-1].removeprefix('nixos-')
                if name == 't3':
                    return 'nixos-t3:' + deployment.t3_build_tag(ROOT) + ' healthy'
                if name == 'reviewer':
                    return 'nixos-t3:' + deployment.t3_build_tag(ROOT) + ' running'
                if name == 'hindsight':
                    return deployment.HINDSIGHT_IMAGE + ' healthy'
                return images[name]['image'] + ' healthy'
            if argv[-4:] == ['config', 'get', '--json', 'mcp_servers.t3']:
                return '{"url": "http://127.0.0.1:3773/mcp", "enabled": true}'
            if argv == ['ss', '-lntH']:
                return '\n'.join('LISTEN 0 128 127.0.0.1:' + port + ' 0.0.0.0:*'
                                 for port in ('18642', '19119', '13100', '3773', '18888'))
            return ''
        return healthy

    def test_failed_rollout_preserves_native_state_and_successful_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            memory = state / 'hermes/memory.md'
            receipt = state / 'deployment.json'
            receipt.write_text('{"revision":"previous-good-revision"}\n')
            previous = receipt.read_bytes()

            def unhealthy(argv):
                if 'up' in argv:
                    raise subprocess.CalledProcessError(1, ['docker', 'compose', 'up'])
                return ''

            with self.assertRaises(subprocess.CalledProcessError):
                apply('a' * 40, ROOT, state, Path(td) / 'current', run=unhealthy)
            self.assertEqual(previous, receipt.read_bytes())
            self.assertEqual('Learning must survive failed deployment', memory.read_text())
            self.assertEqual('failed', json.loads((state / 'deployment-attempt.json').read_text())['status'])

    def test_success_records_only_after_native_readback_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls = []
            current = Path(td) / 'current'
            apply('b' * 40, ROOT, state, current, run=self.healthy_runner(calls))
            receipt = json.loads((state / 'deployment.json').read_text())
            self.assertEqual('b' * 40, receipt['revision'])
            self.assertEqual('healthy', receipt['status'])
            self.assertEqual(ROOT, current.resolve())
            self.assertEqual('Learning must survive failed deployment', (state / 'hermes/memory.md').read_text())
            self.assertTrue(any('unittest' in call for call in calls))
            self.assertFalse(any('down' in call or '--volumes' in call for call in calls))

    def test_memory_redaction_probe_runs_on_the_new_hermes_image_before_anything_starts(self):
        image = json.loads((ROOT / 'deploy/versions.json').read_text())['hermes']['image']
        for verdict in ('redaction-probe: ok', 'redaction-probe: failed: leaks: jwt', ''):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                calls, healthy = [], self.healthy_runner([])

                def run(argv):
                    calls.append(argv)
                    if argv[:2] == ['docker', 'run'] and '--network' in argv:
                        return verdict
                    return healthy(argv)
                probe = lambda source, img, runner: deployment.probe_hermes_redaction(source, img, runner)
                if verdict.endswith(': ok'):
                    apply('b' * 40, ROOT, state, Path(td) / 'current', run=run, redaction=probe)
                else:
                    with self.assertRaisesRegex(ValueError, 'scrub secrets'):
                        apply('b' * 40, ROOT, state, Path(td) / 'current', run=run, redaction=probe)
                probes = [i for i, c in enumerate(calls) if c[:2] == ['docker', 'run'] and '--network' in c]
                self.assertEqual(1, len(probes))
                self.assertIn(image, calls[probes[0]])
                self.assertEqual(['none'], [calls[probes[0]][calls[probes[0]].index('--network') + 1]])
                ups = [i for i, c in enumerate(calls) if 'up' in c and c[:2] == ['docker', 'compose']]
                if verdict.endswith(': ok'):
                    self.assertLess(probes[0], ups[0])
                else:
                    self.assertEqual([], ups)  # nothing running was changed

    def test_invalid_active_memory_refuses_before_compose_up(self):
        for problem in ('wrong bank', 'missing plugin'):
            with self.subTest(problem), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                deployment.ensure_hindsight_state(state)
                config = state / 'hermes/hindsight/config.json'
                config.parent.mkdir()
                bank = 'personal' if problem == 'wrong bank' else 'lnx'
                config.write_text(json.dumps(deployment.hindsight_profile_config(
                    'default', bank, deployment.hindsight_api_key(state))))
                calls = []
                healthy = self.healthy_runner([])

                def run(argv):
                    calls.append(argv)
                    if argv[:2] == ['docker', 'run'] and 'HERMES_HOME=/opt/data' in argv:
                        self.assertIn('--network', argv)
                        self.assertIn(f'type=bind,source={state}/hermes,target=/opt/data,readonly', argv)
                        return '["hindsight", false]'
                    return healthy(argv)
                with self.assertRaisesRegex(ValueError, 'own local Hindsight bank|reviewed commit'):
                    apply('b' * 40, ROOT, state, Path(td) / 'current', run=run,
                          memory_preflight=deployment.preflight_memory)
                self.assertFalse(any('up' in call or 'restart' in call for call in calls))

    def test_changed_native_inputs_refuse_before_running_commands(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            (state / 'installed-inputs.json').write_text('{}')
            (state / 'deployment-attempt.json').write_text('{"status":"healthy","revision":"previous"}')
            calls = []
            with self.assertRaisesRegex(ValueError, 'Native inputs changed'):
                apply('c' * 40, ROOT, state, Path(td) / 'current', run=calls.append)
            self.assertEqual([], calls)
            self.assertFalse((state / 'deployment.json').exists())
            attempt = json.loads((state / 'deployment-attempt.json').read_text())
            self.assertEqual('failed', attempt['status'])
            self.assertEqual('c' * 40, attempt['revision'])

    def test_interrupted_source_pointer_can_be_reconciled(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            current = Path(td) / 'current'
            current.with_name('current.next').symlink_to('/abandoned/release')
            apply('d' * 40, ROOT, state, current, run=self.healthy_runner([]))
            self.assertEqual(ROOT, current.resolve())
            self.assertFalse(current.with_name('current.next').is_symlink())
            self.assertEqual('healthy', json.loads((state / 'deployment-attempt.json').read_text())['status'])

    def test_t3_is_built_pinned_isolated_and_credential_renewal_restarts_hermes(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls = []
            apply('f' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner(calls),
                  t3_credential=lambda s: True)
            self.assertTrue((state / 't3/home').is_dir() and (state / 't3/work').is_dir())
            self.assertIn(['docker', 'image', 'ls', '--quiet', 'nixos-t3:' + deployment.t3_build_tag(ROOT)], calls)
            self.assertFalse(any('build' in c for c in calls))  # tag present: never rebuilt
            self.assertIn(['docker', 'restart', '--timeout', '60', 'nixos-hermes'], calls)
            env_written = [c for c in calls if 'config' in c]
            self.assertTrue(env_written)
        compose = (ROOT / 'deploy/compose.yaml').read_text()
        t3 = compose.split('  t3:', 1)[1]
        self.assertIn('127.0.0.1:3773:3773', t3)
        self.assertIn('cap_drop: [ALL]', t3)
        self.assertNotIn('docker.sock', compose)
        self.assertNotIn('network_mode: host', t3)
        self.assertNotIn('\n    build:', t3)  # a per-release build context would recreate T3 every deploy
        self.assertNotIn('/hermes', t3.split('networks:\n  coding')[0])

    def test_missing_t3_image_is_built_from_the_release(self):
        calls = []
        def runner(argv):
            calls.append(argv)
            return ''  # tag absent
        deployment.ensure_t3_image(ROOT, runner)
        self.assertEqual(['docker', 'build', '--tag', 'nixos-t3:' + deployment.t3_build_tag(ROOT),
                          str(ROOT / 'deploy/t3')], calls[-1])
        def daemon_down(argv):
            raise subprocess.CalledProcessError(1, argv)
        with self.assertRaises(subprocess.CalledProcessError):
            deployment.ensure_t3_image(ROOT, daemon_down)

    def test_t3_state_paths_must_be_plain_directories(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            (state / 't3').symlink_to(td)
            with self.assertRaisesRegex(ValueError, 'T3 state path'):
                apply('a' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))

    def test_versions_manifest_matches_t3_dockerfile_pins(self):
        versions = json.loads((ROOT / 'deploy/versions.json').read_text())['t3']
        dockerfile = (ROOT / 'deploy/t3/Dockerfile').read_text()
        self.assertIn('ARG T3_VERSION=' + versions['version'], dockerfile)
        self.assertIn('ARG T3_SHA256=' + versions['sha256'], dockerfile)
        names = {'claude-code': 'CLAUDE_CODE', 'codex': 'CODEX', 'opencode': 'OPENCODE',
                 'antigravity-cli': 'AGY', 'grok': 'GROK', 'gh': 'GH'}
        for key, arg in names.items():
            self.assertIn(f'ARG {arg}_VERSION={versions["harnesses"][key]}', dockerfile)

    def test_installed_recovery_units_follow_the_reviewed_release(self):
        with tempfile.TemporaryDirectory() as td:
            units = Path(td) / 'units'
            units.mkdir()
            calls = []
            self.assertFalse(deployment.sync_recovery_units(ROOT, calls.append, units))  # not adopted: untouched
            self.assertEqual([], list(units.iterdir()))
            (units / 'nixos-recovery.service').write_text('old hook without t3')
            self.assertTrue(deployment.sync_recovery_units(ROOT, calls.append, units))
            self.assertEqual((ROOT / 'automation/recovery/nixos-recovery.service').read_text(),
                             (units / 'nixos-recovery.service').read_text())
            self.assertEqual([['systemctl', 'daemon-reload']], calls)
            self.assertFalse((units / 'nixos-recovery.timer').exists())

    def test_t3_registration_retries_disabled_server_after_sharing_credential(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            profile = state / 'hermes/profiles/demo'
            profile.mkdir(parents=True)
            (profile / 'nix-venture.json').write_text('{"venture": "demo"}')
            calls = []
            healthy = self.healthy_runner(calls)

            def run(argv):
                if argv[-4:] == ['config', 'get', '--json', 'mcp_servers.t3']:
                    self.assertIn(['credential shared'], calls)
                    return json.dumps({'url': 'http://127.0.0.1:3773/mcp', 'enabled': False,
                                       'headers': {'Authorization': 'masked'}})
                if argv[-3:] == ['mcp', 'test', 't3']:
                    return '✓ Tools discovered: 59'
                if argv[-4:] == ['config', 'get', '--json', 'mcp_servers.t3.enabled']:
                    self.assertTrue(any(call[-5:] == ['config', 'set', '--force', 'mcp_servers.t3.enabled', 'true']
                                        for call in calls))
                    return 'true'
                return healthy(argv)

            def share(_):
                calls.append(['credential shared'])
                return False

            apply('a' * 40, ROOT, state, Path(td) / 'current', run=run, t3_credential=share)
            self.assertIn(['docker', 'restart', '--timeout', '60', 'nixos-hermes'], calls)

    def test_t3_registration_preserves_custom_filters_and_retries_pending_reload(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            profile = state / 'hermes/profiles/demo'
            profile.mkdir(parents=True)
            (profile / 'nix-venture.json').write_text('{"venture": "demo"}')
            pending = state / '.t3-registration-reload'
            pending.touch()
            calls = []
            healthy = self.healthy_runner(calls)

            def run(argv):
                if argv[-4:] == ['config', 'get', '--json', 'mcp_servers.t3']:
                    return json.dumps({'url': 'http://127.0.0.1:3773/mcp', 'enabled': False,
                                       'headers': {'Authorization': 'masked'},
                                       'tools': {'include': ['orchestrator_capabilities']}})
                return healthy(argv)

            apply('a' * 40, ROOT, state, Path(td) / 'current', run=run)
            self.assertFalse(any('mcp' in call or 'set' in call for call in calls))
            self.assertIn(['docker', 'restart', '--timeout', '60', 'nixos-hermes'], calls)
            self.assertFalse(pending.exists())

    def test_os_skills_sync_only_owned_category_for_lnx_and_venture_leads(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            hermes = state / 'hermes'
            (hermes / 'skills/brainstorming').mkdir(parents=True)
            (hermes / 'skills/brainstorming/SKILL.md').write_text('kit-owned')
            (hermes / 'skills/nix-os/stale-skill').mkdir(parents=True)
            for name, venture in (('acme', True), ('engineer', False)):
                (hermes / 'profiles' / name / 'skills').mkdir(parents=True)
                if venture:
                    (hermes / 'profiles' / name / 'nix-venture.json').write_text('{"venture": "acme"}')
            apply('e' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))
            for skills in (hermes / 'skills', hermes / 'profiles/acme/skills'):
                self.assertEqual({'venture-factory', 'venture-lead', 't3-delegate', 'crm-lead-ops', 'company-ops'}, {p.name for p in (skills / 'nix-os').iterdir()})
                self.assertEqual((ROOT / 'skills/venture-lead/SKILL.md').read_text(),
                                 (skills / 'nix-os/venture-lead/SKILL.md').read_text())
                self.assertEqual([], [p.name for p in skills.iterdir() if p.name.startswith('.nix-os')])
            self.assertFalse((hermes / 'profiles/engineer/skills/nix-os').exists())
            self.assertEqual('kit-owned', (hermes / 'skills/brainstorming/SKILL.md').read_text())

    def fake_hermes(self, state, calls, drop=(), fail=None, on_call=None):
        """Run a fake Hermes CLI against the disposable mount, with real shell redirection."""
        store = {}

        def run(argv):
            calls.append(argv)
            self.assertEqual(['docker', 'run'], argv[:2])
            mount = argv[argv.index('--mount') + 1]
            home = Path(mount.removeprefix('type=bind,source=').removesuffix(',target=/opt/data'))
            self.assertTrue(home.is_relative_to(state / 'provisioning'))
            profiles = home / 'profiles'
            entry = argv.index('--entrypoint')
            program, args = argv[entry + 1], argv[entry + 3:]
            args = ['hermes' if program.endswith('/bin/hermes') else program, *args]

            def local(path):
                return home / path.removeprefix('/opt/data/')

            if on_call:
                on_call(args)
            if fail and fail(args):
                raise subprocess.CalledProcessError(1, argv)
            if args[0] == 'sh':  # SOUL.md write as the Hermes user
                _, _, script, _, text, path = args
                subprocess.run(['sh', '-c', script, 'write-soul', text, str(local(path))],
                               check=True, capture_output=True, text=True)
                return ''
            if args[0] == 'cat':
                return local(args[1]).read_text().strip()
            args = args[1:]  # past `hermes`
            if args[:2] == ['profile', 'create']:
                (profiles / args[2]).mkdir(parents=True)
                (profiles / args[2] / 'SOUL.md').write_text('hermes default soul')
            elif args[:2] == ['profile', 'rename']:
                (profiles / args[2]).rename(profiles / args[3])
                for key in [k for k in store if k[0] == args[2]]:
                    store[(args[3], key[1])] = store.pop(key)
            elif args[2:4] == ['config', 'set']:
                store[(args[1], args[4])] = 'true' if args[4] in drop else args[5]  # a setting Hermes silently ignores
            elif args[2:4] == ['config', 'get']:
                if (args[1], args[5]) not in store:
                    raise subprocess.CalledProcessError(1, argv)
                value = store[(args[1], args[5])]
                return json.dumps(False if value == 'false' else value)
            return ''
        return run

    def profile_commands(self, calls):
        return [c[c.index('--entrypoint') + 3:] for c in calls
                if c[c.index('--entrypoint') + 1].endswith('/bin/hermes')]

    def record(self, state):
        return json.loads((state / 'provisioning/personal.json').read_text())

    def test_personal_profile_is_built_under_a_staging_name_and_renamed_last(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls, early = [], []

            def watch(args):
                early.append((state / 'hermes/profiles/personal').exists())
            self.assertEqual('created', deployment.ensure_personal_profile(
                ROOT, state, self.fake_hermes(state, calls, on_call=watch)))
            self.assertEqual([False] * len(early), early)
            manifest = json.loads((ROOT / 'deploy/profiles/personal.json').read_text())
            pilot = json.loads((ROOT / 'deploy/pilot.json').read_text())
            profile = state / 'hermes/profiles/personal'
            self.assertFalse((state / 'hermes/profiles/personalsetup').exists())
            self.assertEqual((ROOT / manifest['role_file']).read_text(), (profile / 'SOUL.md').read_text())
            self.assertTrue((state / 'hermes/workspaces/personal').is_dir())
            commands = self.profile_commands(calls)
            self.assertEqual(['profile', 'create', 'personalsetup', '--no-alias'], commands[0])  # no description: not routable
            sets = [c for c in commands if c[2:4] == ['config', 'set']]
            self.assertEqual({'personalsetup'}, {c[1] for c in sets})
            self.assertEqual('platforms.discord.enabled', sets[0][4])  # restrictive keys first
            self.assertEqual('false', sets[0][5])
            by_key = {c[4]: c[5] for c in sets}
            self.assertEqual(pilot['model']['provider'], by_key['model.provider'])
            self.assertEqual(pilot['model']['default'], by_key['model.default'])
            self.assertEqual('manual', by_key['approvals.mode'])
            self.assertEqual('deny', by_key['approvals.cron_mode'])
            self.assertEqual(['profile', 'rename', 'personalsetup', 'personal'], commands[-2])
            self.assertEqual(['profile', 'describe', 'personal', '--text', manifest['description']], commands[-1])
            # Shared login only: no clone (would copy .env/credentials), no venture marker, no secrets in the manifest.
            for forbidden in ('--clone', '--api-key', 'auth.json'):
                self.assertNotIn(forbidden, json.dumps(calls))
            self.assertFalse((profile / 'nix-venture.json').exists())
            self.assertEqual([], [p.name for p in profile.iterdir() if p.name.startswith('.nix') or p.name == 'nix-profile.json'])
            text = (ROOT / 'deploy/profiles/personal.json').read_text().lower()
            for word in ('token":', 'password', 'secret":', '@'):
                self.assertNotIn(word, text)

    def test_personal_ownership_record_is_root_private_and_outside_the_hermes_home(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            record = state / 'provisioning/personal.json'
            self.assertEqual(0o600, record.stat().st_mode & 0o777)
            self.assertEqual(0o700, record.parent.stat().st_mode & 0o777)
            self.assertEqual('complete', self.record(state)['state'])
            self.assertFalse(list((state / 'hermes').rglob('personal.json')))
            calls = []  # complete: never touched again, even if the profile is later removed
            (state / 'hermes/profiles/personal').rename(state / 'hermes/profiles/gone')
            self.assertEqual('present', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls)))
            self.assertEqual([], calls)

    def test_incomplete_personal_profile_is_never_reachable_as_personal_and_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls = []
            with self.assertRaises(ValueError):  # create ran, the Discord readback failed
                deployment.ensure_personal_profile(
                    ROOT, state, self.fake_hermes(state, calls, drop=('platforms.discord.enabled',)))
            self.assertFalse((state / 'hermes/profiles/personal').exists())
            self.assertFalse((state / 'hermes/workspaces/personal').exists())
            self.assertFalse((state / 'hermes/profiles/personalsetup').exists())
            self.assertFalse(list((state / 'provisioning').iterdir()))
            calls.clear()
            self.assertEqual('created', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls)))
            profile = state / 'hermes/profiles/personal'
            self.assertEqual((ROOT / 'deploy/roles/personal.md').read_text(), (profile / 'SOUL.md').read_text())
            self.assertFalse((state / 'hermes/profiles/personalsetup').exists())
            self.assertEqual('complete', self.record(state)['state'])

    def test_personal_provisioning_failures_before_rename_leave_no_personal_profile(self):
        for label, fail in (
                ('role write', lambda a: a[0] == 'sh'),
                ('role read back', lambda a: a[0] == 'cat'),
                ('model setting', lambda a: a[1:3] == ['-p', 'personalsetup'] and a[-2:-1] == ['model.default'] and a[3:5] == ['config', 'set'])):
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                with self.assertRaises(subprocess.CalledProcessError):
                    deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, [], fail=fail))
                self.assertFalse((state / 'hermes/profiles/personal').exists())
                self.assertFalse(list((state / 'provisioning').iterdir()))

    def test_personal_rename_and_describe_failures_leave_no_live_changes(self):
        for step in ('rename', 'describe'):
            with self.subTest(step), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                broken = lambda a: a[1:3] == ['profile', step]
                with self.assertRaises(subprocess.CalledProcessError):
                    deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, [], fail=broken))
                self.assertFalse((state / 'hermes/profiles/personal').exists())
                self.assertFalse((state / 'hermes/workspaces/personal').exists())
                self.assertEqual('created', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, [])))
                self.assertEqual('complete', self.record(state)['state'])

    def test_interrupted_publication_reuses_only_the_recorded_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            rename = deployment._rename_directory_noreplace

            def interrupt(src_fd, src, dst_fd, dst):
                if src == 'personal':
                    raise OSError('interrupted profile publication')
                rename(src_fd, src, dst_fd, dst)
            with patch.object(deployment, '_rename_directory_noreplace', side_effect=interrupt):
                with self.assertRaises(OSError):
                    deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            workspace = state / 'hermes/workspaces/personal'
            self.assertEqual('prepared', self.record(state)['state'])
            self.assertEqual(deployment._directory_identity(workspace), self.record(state)['workspace'])
            workspace.chmod(0o750)
            self.assertEqual('resumed', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, [])))
            self.assertEqual(0o750, workspace.stat().st_mode & 0o777)
            self.assertEqual('complete', self.record(state)['state'])

    def test_interrupted_record_update_recovers_without_touching_the_live_profile(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            save = deployment._save_personal_record

            def interrupt(state, name, phase, **fields):
                if phase == 'complete':
                    raise OSError('interrupted record update')
                save(state, name, phase, **fields)
            with patch.object(deployment, '_save_personal_record', side_effect=interrupt):
                with self.assertRaises(OSError):
                    deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            self.assertEqual('prepared', self.record(state)['state'])
            calls = []
            self.assertEqual('resumed', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls)))
            self.assertEqual([], calls)
            self.assertEqual('complete', self.record(state)['state'])

    def test_existing_personal_profile_is_never_touched_or_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            profile = state / 'hermes/profiles/personal'
            (profile / 'memories').mkdir(parents=True)
            (profile / 'SOUL.md').write_text('operator edited')
            (profile / 'memories/MEMORY.md').write_text('private')
            # The Hermes user can write anything inside the profile, including the old in-profile proofs.
            (profile / '.nix-profile-creating').write_text('forged\n')
            (profile / 'nix-profile.json').write_text('{"profile": "personal"}')
            calls = []
            self.assertEqual('present', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls)))
            self.assertEqual([], calls)
            self.assertEqual('operator edited', (profile / 'SOUL.md').read_text())
            self.assertFalse((state / 'provisioning').exists())
        with tempfile.TemporaryDirectory() as td:  # a forged record cannot be bound to a profile it did not stage
            state = self.prepare_state(td)
            (state / 'hermes/profiles/personal').mkdir(parents=True)
            (state / 'hermes/profiles/personalsetup').mkdir()
            deployment._save_personal_record(state, 'personal', 'staged', identity=[1, 2])
            calls = []
            self.assertEqual('present', deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls)))
            self.assertEqual([], calls)

    def test_existing_live_staging_profile_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as td:  # staging name taken by something deployment never recorded
            state = self.prepare_state(td)
            (state / 'hermes/profiles/personalsetup').mkdir()
            calls = []
            with self.assertRaises(ValueError):
                deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, calls))
            self.assertEqual([], calls)
            self.assertFalse((state / 'hermes/profiles/personal').exists())

    def test_live_staging_replacement_is_never_configured_or_published(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            replacement = state / 'hermes/profiles/personalsetup'

            def swap(args):
                if args[-2:-1] == ['platforms.discord.enabled'] and args[3:5] == ['config', 'set']:
                    replacement.mkdir()
                    (replacement / 'SOUL.md').write_text('replacement')
            self.assertEqual('created', deployment.ensure_personal_profile(
                ROOT, state, self.fake_hermes(state, [], on_call=swap)))
            target = state / 'hermes/profiles/personal'
            self.assertNotEqual(replacement.stat().st_ino, target.stat().st_ino)
            self.assertEqual('replacement', (replacement / 'SOUL.md').read_text())
            self.assertEqual((ROOT / 'deploy/roles/personal.md').read_text(), (target / 'SOUL.md').read_text())

    def test_personal_publication_never_replaces_a_concurrent_destination(self):
        for parent in ('profiles', 'workspaces'):
            with self.subTest(parent), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                target = state / 'hermes' / parent / 'personal'
                rename = deployment._rename_directory_noreplace
                reached = []

                def occupy(src_fd, src, dst_fd, dst):
                    if (src == 'personal') == (parent == 'profiles'):
                        target.mkdir()
                        reached.append(target.stat().st_ino)
                    rename(src_fd, src, dst_fd, dst)
                with patch.object(deployment, '_rename_directory_noreplace', side_effect=occupy):
                    with self.assertRaises(FileExistsError):
                        deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
                self.assertEqual([target.stat().st_ino], reached)
                self.assertEqual([], list(target.iterdir()))
                self.assertEqual('prepared', self.record(state)['state'])

    def test_personal_profile_refuses_symlinks_without_touching_their_targets(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            (state / 'hermes/profiles/personal').symlink_to(Path(td))
            with self.assertRaises(ValueError):
                deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
        for link in ('workspaces', 'workspaces/personal'):
            with self.subTest(link), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                victim = Path(td) / 'victim'
                victim.mkdir(mode=0o755)
                victim.chmod(0o755)
                (state / 'hermes' / link).parent.mkdir(parents=True, exist_ok=True)
                (state / 'hermes' / link).symlink_to(victim)
                with self.assertRaises(ValueError):
                    deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
                self.assertEqual(0o755, victim.stat().st_mode & 0o777)  # root never chmod/chowned through the link
                self.assertEqual([], list(victim.iterdir()))
                self.assertFalse((state / 'hermes/profiles/personal').exists())

    def test_personal_workspace_is_private_and_owned_by_the_hermes_user(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            workspace = state / 'hermes/workspaces/personal'
            self.assertEqual(0o700, workspace.stat().st_mode & 0o777)
            self.assertEqual(deployment.HERMES_UID, workspace.stat().st_uid)

    def test_existing_personal_workspace_is_refused_with_ownership_and_mode_intact(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            workspace = state / 'hermes/workspaces/personal'
            workspace.mkdir(parents=True)
            workspace.chmod(0o751)
            before = workspace.stat()
            with self.assertRaises(ValueError):
                deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            after = workspace.stat()
            self.assertEqual((before.st_uid, before.st_gid, before.st_mode),
                             (after.st_uid, after.st_gid, after.st_mode))
            self.assertFalse((state / 'hermes/profiles/personal').exists())

    def release_with(self, td, edit):
        release = Path(td) / 'release'
        shutil.copytree(ROOT, release, ignore=shutil.ignore_patterns('.git', 'node_modules', '__pycache__'))
        edit(release)
        return release

    def test_broken_personal_contract_is_a_handled_failure_not_a_failed_deployment(self):
        def manifest(change):
            def edit(release):
                path = release / 'deploy/profiles/personal.json'
                data = json.loads(path.read_text())
                path.write_text(json.dumps(change(data)))
            return edit

        def pilot(change):
            def edit(release):
                path = release / 'deploy/pilot.json'
                data = json.loads(path.read_text())
                path.write_text(json.dumps(change(data)))
            return edit

        def drop(container, key):
            return lambda data: ({k: v for k, v in data.items() if k != key} if container is None
                                 else {**data, container: {k: v for k, v in data[container].items() if k != key}})
        broken = {
            'no config': manifest(drop(None, 'config')),
            'no profile': manifest(drop(None, 'profile')),
            'no role file': manifest(drop(None, 'role_file')),
            'no model source': manifest(drop('model', 'source')),
            'manifest is a list': manifest(lambda data: [data]),
            'config is a list': manifest(lambda data: {**data, 'config': ['x']}),
            'discord not disabled': manifest(lambda data: {**data, 'config': {**data['config'], 'platforms.discord.enabled': True}}),
            'no manual approval': manifest(drop('config', 'approvals.mode')),
            'approvals loosened': manifest(lambda data: {**data, 'config': {**data['config'], 'approvals.mode': 'off'}}),
            'no cron approval': manifest(drop('config', 'approvals.cron_mode')),
            'cron approval loosened': manifest(lambda data: {**data, 'config': {**data['config'], 'approvals.cron_mode': 'allow'}}),
            'additional config key': manifest(lambda data: {**data, 'config': {**data['config'], 'gateway.multiplex_profiles': True}}),
            'role outside release': manifest(lambda data: {**data, 'role_file': '../../etc/hosts'}),
            'pilot without default': pilot(drop('model', 'default')),
            'pilot without model': pilot(drop(None, 'model')),
            'pilot is a list': pilot(lambda data: [data]),
        }
        for label, edit in broken.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                state = self.prepare_state(td)
                release = self.release_with(td, edit)
                (state / 'installed-inputs.json').write_text(json.dumps(deployment.native_inputs(release)))
                with self.assertRaises(ValueError):
                    deployment.ensure_personal_profile(release, state, self.fake_hermes(state, []))
                calls, stderr = [], io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    apply('d' * 40, release, state, Path(td) / 'current', run=self.healthy_runner(calls),
                          personal=deployment.ensure_personal_profile)
                self.assertEqual('healthy', json.loads((state / 'deployment.json').read_text())['status'])
                self.assertIn('Personal profile provisioning failed', stderr.getvalue())
                self.assertFalse((state / 'hermes/profiles/personalsetup').exists())
                self.assertFalse((state / 'hermes/profiles/personal').exists())

    def test_personal_profile_failure_never_fails_the_deployment_and_is_not_a_company_target(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            seen = []

            def broken(source, st, run):
                seen.append(True)
                raise subprocess.CalledProcessError(1, ['hermes', 'SECRET-ROLE-TEXT'])

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                apply('c' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]), personal=broken)
            self.assertEqual([True], seen)
            self.assertNotIn('SECRET-ROLE-TEXT', stderr.getvalue())
            self.assertEqual('healthy', json.loads((state / 'deployment.json').read_text())['status'])
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            deployment.ensure_personal_profile(ROOT, state, self.fake_hermes(state, []))
            (state / 'hermes/profiles/personal/skills').mkdir()
            self.assertNotIn(state / 'hermes/profiles/personal/skills', deployment.os_skill_targets(state))
            self.assertNotIn(state / 'hermes/profiles/personal', _real_venture_profiles(state))

    def test_os_skill_sync_never_follows_symlinked_profiles_and_recovers_interrupted_swap(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            hermes = state / 'hermes'
            (hermes / 'skills').mkdir()
            outside = Path(td) / 'outside'
            (outside / 'skills').mkdir(parents=True)
            (outside / 'nix-venture.json').write_text('{"venture": "evil"}')
            (hermes / 'profiles/evil').symlink_to(outside)
            (hermes / 'profiles/clone/skills').mkdir(parents=True)
            (hermes / 'profiles/clone/nix-venture.json').write_text('{"venture": "acme"}')
            # interrupted previous run: live category moved aside, never restored
            (hermes / 'skills/.nix-os.prev/venture-lead').mkdir(parents=True)
            apply('9' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))
            self.assertEqual([], list((outside / 'skills').iterdir()))
            self.assertTrue((hermes / 'skills/nix-os/venture-factory/SKILL.md').is_file())
            self.assertFalse((hermes / 'skills/.nix-os.prev').exists())
            self.assertFalse((hermes / 'profiles/clone/skills/nix-os').exists())

    def test_crm_is_opt_in_and_its_secrets_are_generated_once(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls = []
            apply('1' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner(calls))
            self.assertFalse(any('--profile' in c for c in calls))
            self.assertFalse((state / 'twenty').exists())
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            (state / 'twenty').mkdir()
            (state / 'twenty/ENABLED').write_text('needed by a venture')
            deployment.TWENTY_DB_OWNER = deployment.TWENTY_APP_OWNER = (os.getuid(), os.getgid())
            calls = []
            runner = self.healthy_runner(calls)
            def with_crm(argv):
                if argv == ['ss', '-lntH']:
                    return runner(argv) + '\nLISTEN 0 128 127.0.0.1:3020 0.0.0.0:*'
                if argv[-1] == 'nixos-twenty-server':
                    calls.append(argv)
                    return 'healthy'
                return runner(argv)
            apply('2' * 40, ROOT, state, Path(td) / 'current', run=with_crm)
            self.assertTrue(any(c[-2:] == ['--profile', 'crm'] or '--profile' in c for c in calls))
            server = (state / 'twenty/server.env').read_text()
            self.assertIn('APP_SECRET=', server)
            self.assertEqual(0o600, (state / 'twenty/server.env').stat().st_mode & 0o777)
            apply('3' * 40, ROOT, state, Path(td) / 'current', run=with_crm)
            self.assertEqual(server, (state / 'twenty/server.env').read_text())  # never regenerated
            (state / 'twenty/db.env').unlink()
            with self.assertRaisesRegex(ValueError, 'half-present'):
                apply('4' * 40, ROOT, state, Path(td) / 'current', run=with_crm)
            (state / 'twenty/server.env').unlink()
            (state / 'twenty/db/PG_VERSION').write_text('16')
            with self.assertRaisesRegex(ValueError, 'never regenerate'):
                apply('5' * 40, ROOT, state, Path(td) / 'current', run=with_crm)

    def test_disabling_crm_stops_running_twenty_containers(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            calls = []
            runner = self.healthy_runner(calls)
            def with_leftover(argv):
                if argv[:2] == ['docker', 'ps']:
                    calls.append(argv)
                    return 'nixos-twenty-db\nnixos-twenty-worker'
                return runner(argv)
            apply('6' * 40, ROOT, state, Path(td) / 'current', run=with_leftover)
            stops = [c[-1] for c in calls if c[:2] == ['docker', 'stop'] and 'twenty' in c[-1]]
            self.assertEqual(['nixos-twenty-worker', 'nixos-twenty-db'], stops)
            self.assertFalse(any('rm' in c or 'down' in c for c in calls))
    def test_health_publication_refuses_symlinked_nix_and_never_fails_deploy(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            outside = Path(td) / 'outside'
            outside.mkdir()
            nix = state / 'hermes/nix'
            nix.rename(Path(td) / 'real-nix')
            nix.symlink_to(outside)
            deployment.publish_health(state, {'status': 'healthy', 'revision': 'r'})  # no exception
            self.assertEqual([], list(outside.iterdir()))

    def test_paperclip_is_stopped_never_removed_and_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            (state / 'paperclip/archived.db').write_text('history')
            calls = []
            runner = self.healthy_runner(calls)
            def paperclip_running(argv):
                if argv[:2] == ['docker', 'ps'] and 'name=^nixos-paperclip$' in argv:
                    calls.append(argv)
                    return 'pc123'
                return runner(argv)
            apply('7' * 40, ROOT, state, Path(td) / 'current', run=paperclip_running)
            self.assertIn(['docker', 'stop', '--time', '90', 'nixos-paperclip'], calls)
            self.assertFalse(any(c[:2] == ['docker', 'rm'] or 'down' in c for c in calls))
            self.assertEqual('history', (state / 'paperclip/archived.db').read_text())
        self.assertNotIn('paperclip:', (ROOT / 'deploy/compose.yaml').read_text())

    def test_reviewer_state_script_and_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            apply('8' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))
            root = state / 'reviewer'
            self.assertEqual((ROOT / 'deploy/reviewer/reviewer.mjs').read_text(), (root / 'app/reviewer.mjs').read_text())
            self.assertEqual((ROOT / '.github/CODEOWNERS').read_text(), (root / 'app/CODEOWNERS').read_text())
            self.assertEqual(0o700, (root / 'approvals').stat().st_mode & 0o777)
            self.assertEqual(os.getuid(), (root / 'approvals').stat().st_uid)
            self.assertIn('REVIEW_POOL=', (root / 'reviewer.env').read_text())
            (root / 'reviewer.env').write_text('REVIEW_APP_ID=42\n')
            apply('9' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))
            self.assertEqual('REVIEW_APP_ID=42\n', (root / 'reviewer.env').read_text())  # settings never overwritten
        reviewer = (ROOT / 'deploy/compose.yaml').read_text().split('  reviewer:', 1)[1].split('\n  # Optional CRM', 1)[0]
        for required in ('networks: [review]', 'cap_drop: [ALL]',
                         'target: /run/reviewer-approvals\n        read_only: true'):
            self.assertIn(required, reviewer)
        for forbidden in ('ports:', 'docker.sock', '/t3/home', 'network_mode'):
            self.assertNotIn(forbidden, reviewer)

    def test_launcher_rejects_shell_commands_refs_and_malformed_hashes(self):
        for value in ('main', 'a' * 39, 'a' * 41, 'a' * 40 + '; id', '--help', '$(id)'):
            with self.subTest(value=value):
                result = subprocess.run(['sh', str(ROOT / 'scripts/deploy-os.sh'), value],
                                        capture_output=True, text=True)
                self.assertNotEqual(0, result.returncode)
