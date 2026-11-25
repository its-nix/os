import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment', ROOT / 'scripts/deploy-os.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
deployment.T3_UID, deployment.T3_GID = os.getuid(), os.getgid()
deployment.ROOT_UID, deployment.ROOT_GID = os.getuid(), os.getgid()  # tests run unprivileged; production chowns to the T3 service user
deployment.HINDSIGHT_UID, deployment.HINDSIGHT_GID = os.getuid(), os.getgid()


_real_apply = deployment.apply


def apply(*args, **kwargs):
    kwargs.setdefault('t3_credential', lambda state: False)
    kwargs.setdefault('t3_loaded', lambda state: None)
    kwargs.setdefault('units', lambda source, run: False)
    kwargs.setdefault('memory', lambda state, run: state / '.hindsight-plugin-reload')  # see test_hindsight_memory
    kwargs.setdefault('login', lambda source: None)
    return _real_apply(*args, **kwargs)


class DeploymentTests(unittest.TestCase):
    def prepare_state(self, td):
        state = Path(td) / 'state'
        (state / 'hermes/nix').mkdir(parents=True)
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

    def test_os_skill_sync_never_follows_symlinked_profiles_and_recovers_interrupted_swap(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.prepare_state(td)
            hermes = state / 'hermes'
            (hermes / 'skills').mkdir()
            outside = Path(td) / 'outside'
            (outside / 'skills').mkdir(parents=True)
            (outside / 'nix-venture.json').write_text('{"venture": "evil"}')
            (hermes / 'profiles').mkdir()
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
            self.assertIn('REVIEW_POOL=', (root / 'reviewer.env').read_text())
            (root / 'reviewer.env').write_text('REVIEW_APP_ID=42\n')
            apply('9' * 40, ROOT, state, Path(td) / 'current', run=self.healthy_runner([]))
            self.assertEqual('REVIEW_APP_ID=42\n', (root / 'reviewer.env').read_text())  # settings never overwritten
        reviewer = (ROOT / 'deploy/compose.yaml').read_text().split('  reviewer:', 1)[1].split('\n  # Optional CRM', 1)[0]
        for required in ('networks: [review]', 'cap_drop: [ALL]', 'read_only: true'):
            self.assertIn(required, reviewer)
        for forbidden in ('ports:', 'docker.sock', '/t3/home', 'network_mode'):
            self.assertNotIn(forbidden, reviewer)

    def test_launcher_rejects_shell_commands_refs_and_malformed_hashes(self):
        for value in ('main', 'a' * 39, 'a' * 41, 'a' * 40 + '; id', '--help', '$(id)'):
            with self.subTest(value=value):
                result = subprocess.run(['sh', str(ROOT / 'scripts/deploy-os.sh'), value],
                                        capture_output=True, text=True)
                self.assertNotEqual(0, result.returncode)
