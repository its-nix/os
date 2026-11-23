import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_create_repository_supplies_required_namespace_and_private_flag(self):
        module = self.module()
        class Hub:
            username = 'example'
            created = None
            def request(self, method, path, body=None):
                if method == 'POST':
                    self.created = body
                    return {}
                if self.created is None:
                    raise module.Refusal('Docker Hub request failed (HTTP 404)')
                return {'is_private': True}
        hub = Hub()
        module.create_private('docker.io/example/nixos-recovery', hub)
        self.assertEqual('example', hub.created['namespace'])
        self.assertIs(True, hub.created['is_private'])

    def test_snapshot_failure_restarts_both_services(self):
        module = self.module()
        calls = []
        def command(argv):
            calls.append(argv)
            if 'inspect' in argv:
                return 'exited 0' if 'ExitCode' in argv[3] else 'running'
            return ''
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), patch.object(module, 'wait_services') as health:
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                with module.stopped_services(Path(td) / 'deployment.lock'):
                    raise RuntimeError('capture failed')
        self.assertEqual([['docker', 'start', n] for n in reversed(module.CONTAINERS)],
                         [c for c in calls if c[:2] == ['docker', 'start']])
        health.assert_called_once()

    def test_failed_stop_still_restarts_both_services(self):
        module = self.module()
        calls = []
        def command(argv):
            calls.append(argv)
            if 'inspect' in argv:
                return 'running'
            if 'stop' in argv:
                raise module.Refusal('partial stop')
            return ''
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), patch.object(module, 'wait_services'):
            with self.assertRaises(module.Refusal):
                with module.stopped_services(Path(td) / 'deployment.lock'):
                    self.fail('Capture cannot run after failed stop')
        self.assertEqual([['docker', 'start', n] for n in reversed(module.CONTAINERS)],
                         [c for c in calls if c[:2] == ['docker', 'start']])

    def test_database_probe_attaches_sql_stdin_without_network(self):
        module = self.module()
        with patch.object(module, 'run', return_value='NIXOS_RECOVERY_DB_OK') as run:
            module.postgres_check('example@sha256:abc', Path('/scratch/state'))
        argv = run.call_args.args[0]
        self.assertIn('--interactive', argv)
        self.assertEqual('none', argv[argv.index('--network') + 1])
        self.assertIn('SELECT', run.call_args.kwargs['input'])

    def test_public_destination_failure_preserves_previous_success_and_never_captures(self):
        module = self.module()
        class Hub:
            username = 'example'
            def request(self, *args):
                return {'is_private': False}
        with tempfile.TemporaryDirectory() as td:
            root, status = Path(td) / 'live', Path(td) / 'receipts'
            root.mkdir(); status.mkdir()
            previous = status / 'last-success.json'
            previous.write_text('{"status":"published","manifest_digest":"previous"}')
            args = SimpleNamespace(root=root, status=status, publish=True, create_repository=False,
                                   image='docker.io/example/nixos-recovery')
            with patch.object(module, 'Hub', return_value=Hub()), patch.object(module, 'stopped_services') as capture, patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                with self.assertRaises(module.Refusal):
                    module.build(args)
            capture.assert_not_called()
            self.assertEqual('previous', module.json.loads(previous.read_text())['manifest_digest'])
            self.assertEqual('failed', module.json.loads((status / 'last-attempt.json').read_text())['status'])
            # Authored refusal text is recorded so failures are diagnosable without logs.
            self.assertEqual('Recovery repository must exist and be explicitly private',
                             module.json.loads((status / 'last-attempt.json').read_text())['reason'])

    def test_public_repository_refuses_before_push(self):
        path = ROOT / 'automation/recovery/full_create.py'
        self.assertTrue(path.exists(), 'Daily full-state publisher is missing')
        spec = importlib.util.spec_from_file_location('full_create', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        def api(method, path, body=None):
            calls.append((method, path))
            return {'is_private': False}
        with self.assertRaises(module.Refusal):
            module.require_private('docker.io/example/nixos-recovery', api)
        self.assertEqual(1, len(calls))


class CrmRecoveryTests(unittest.TestCase):
    def test_crm_containers_join_capture_only_when_enabled(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(module.CONTAINERS, module.containers(root))
            (root / 'twenty').mkdir()
            (root / 'twenty/ENABLED').write_text('')
            names = module.containers(root)
            self.assertEqual(module.CONTAINERS + module.CRM_CONTAINERS, names)
            # stop app before database; start database before app
            self.assertLess(names.index('nixos-twenty-server'), names.index('nixos-twenty-db'))
            self.assertLess(list(reversed(names)).index('nixos-twenty-db'),
                            list(reversed(names)).index('nixos-twenty-server'))


class ServiceControlTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_worker_without_healthcheck_counts_as_ready_and_sigterm_is_clean(self):
        module = self.module()
        with patch.object(module, 'run', return_value='running'):
            self.assertTrue(module.ready('nixos-twenty-worker'))
        with patch.object(module, 'run', return_value='starting'):
            self.assertFalse(module.ready('nixos-hermes'))
        self.assertIn('exited 143', module.CLEAN_EXIT)

    def test_capture_refuses_running_crm_writers_when_disabled(self):
        module = self.module()
        def command(argv):
            if argv[:2] == ['docker', 'ps']:
                return 'abc123'
            return 'running'
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), \
                patch.object(module, 'crm_enabled', return_value=False):
            with self.assertRaisesRegex(module.Refusal, 'CRM containers run'):
                with module.stopped_services(Path(td) / 'lock'):
                    self.fail('must not capture')


class CrmRestartTests(unittest.TestCase):
    def test_failed_crm_start_still_starts_core_services_and_probe_image_matches_compose(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        started = []
        def command(argv):
            if argv[:2] == ['docker', 'start']:
                started.append(argv[2])
                if argv[2] == 'nixos-twenty-db':
                    raise module.Refusal('failed')
            return ''
        with patch.object(module, 'run', side_effect=command):
            with self.assertRaisesRegex(module.Refusal, 'nixos-twenty-db'):
                module.start_all(list(reversed(module.CONTAINERS + module.CRM_CONTAINERS)))
        self.assertTrue(set(module.CONTAINERS) <= set(started))
        self.assertIn(module.TWENTY_DB_IMAGE, (ROOT / 'deploy/compose.yaml').read_text())
        hook = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        self.assertIn('|| rc=1', hook)
        self.assertNotIn('|| exit 1', hook)


class RecoveryUnitTests(unittest.TestCase):
    def test_failed_backups_cannot_cold_stop_services_indefinitely(self):
        unit = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        self.assertNotIn('StartLimitIntervalSec=0', unit)
        burst = int(unit.split('StartLimitBurst=')[1].split()[0])
        window = unit.split('StartLimitIntervalSec=')[1].split()[0]
        retry = unit.split('RestartSec=')[1].split()[0]
        self.assertEqual(('2h', '15min', 3), (window, retry, burst))
        # The burst (first run + retries) fits in the window, and the window is far shorter than
        # the 24h timer period, so the next daily run is never rejected by the rate limiter.
        self.assertLess((burst - 1) * 15, 120)
        self.assertLess(120, 24 * 60)

    def test_unexpected_exception_text_is_never_recorded(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            root, status = Path(td) / 'live', Path(td) / 'receipts'
            root.mkdir(); status.mkdir()
            args = SimpleNamespace(root=root, status=status, publish=True, create_repository=False,
                                   image='docker.io/example/nixos-recovery')
            with patch.object(module, 'Hub', side_effect=OSError('secret-bearing detail')), \
                    patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                with self.assertRaises(OSError):
                    module.build(args)
            attempt = module.json.loads((status / 'last-attempt.json').read_text())
            self.assertEqual('failed', attempt['status'])
            self.assertNotIn('reason', attempt)
