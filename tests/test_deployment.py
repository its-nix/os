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
deployment.T3_UID, deployment.T3_GID = os.getuid(), os.getgid()  # tests run unprivileged; production chowns to the T3 service user


_real_apply = deployment.apply


def apply(*args, **kwargs):
    kwargs.setdefault('t3_credential', lambda state: False)
    kwargs.setdefault('t3_loaded', lambda state: None)
    kwargs.setdefault('units', lambda source, run: False)
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
            if argv[:2] == ['docker', 'inspect']:
                name = argv[-1].removeprefix('nixos-')
                if name == 't3':
                    return 'nixos-t3:' + deployment.t3_build_tag(ROOT) + ' healthy'
                return images[name]['image'] + ' healthy'
            if argv == ['ss', '-lntH']:
                return '\n'.join('LISTEN 0 128 127.0.0.1:' + port + ' 0.0.0.0:*'
                                 for port in ('18642', '19119', '13100', '3773'))
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
            build = next(c for c in calls if 'build' in c)
            self.assertEqual('t3', build[-1])
            self.assertIn(['docker', 'restart', '--timeout', '60', 'nixos-hermes'], calls)
            env_written = [c for c in calls if 'config' in c]
            self.assertTrue(env_written)
        compose = (ROOT / 'deploy/compose.yaml').read_text()
        t3 = compose.split('  t3:', 1)[1]
        self.assertIn('127.0.0.1:3773:3773', t3)
        self.assertIn('cap_drop: [ALL]', t3)
        self.assertNotIn('docker.sock', compose)
        self.assertNotIn('network_mode: host', t3)
        self.assertNotIn('/hermes', t3.split('networks:\n  coding')[0])

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
                 'gemini-cli': 'GEMINI_CLI', 'grok': 'GROK', 'gh': 'GH'}
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

    def test_launcher_rejects_shell_commands_refs_and_malformed_hashes(self):
        for value in ('main', 'a' * 39, 'a' * 41, 'a' * 40 + '; id', '--help', '$(id)'):
            with self.subTest(value=value):
                result = subprocess.run(['sh', str(ROOT / 'scripts/deploy-os.sh'), value],
                                        capture_output=True, text=True)
                self.assertNotEqual(0, result.returncode)
