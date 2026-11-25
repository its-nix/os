import importlib.util
import json
from pathlib import Path
import os
import re
import subprocess
import sys
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
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), patch.object(module, 'disk_used_percent', return_value=50), patch.object(module, 'compose_files', return_value=[]), patch.object(module, 'wait_services') as health:
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
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), patch.object(module, 'disk_used_percent', return_value=50), patch.object(module, 'compose_files', return_value=[]), patch.object(module, 'wait_services'):
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

    def test_frozen_paperclip_db_is_opened_once_then_skipped_until_bytes_change(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as td:
            restored = Path(td) / 'state'
            db = restored / 'paperclip/instances/default/db'
            db.mkdir(parents=True)
            (db / 'PG_VERSION').write_text('17\n')
            validated = Path(td) / 'paperclip-validated.json'
            with patch.object(module, 'postgres_check') as check:
                self.assertTrue(module.paperclip_check('img@sha256:a', restored, validated))
                self.assertFalse(module.paperclip_check('img@sha256:a', restored, validated))
                self.assertEqual(1, check.call_count)
                (db / 'PG_VERSION').write_text('18\n')
                self.assertTrue(module.paperclip_check('img@sha256:a', restored, validated))
                self.assertTrue(module.paperclip_check('img@sha256:b', restored, validated))
                self.assertTrue(module.paperclip_check('img@sha256:b', restored))  # no record: always
                self.assertEqual(4, check.call_count)

    def test_failed_paperclip_check_records_nothing(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as td:
            restored = Path(td) / 'state'
            (restored / 'paperclip').mkdir(parents=True)
            validated = Path(td) / 'paperclip-validated.json'
            with patch.object(module, 'postgres_check', side_effect=module.Refusal('bad')):
                with self.assertRaises(module.Refusal):
                    module.paperclip_check('img@sha256:a', restored, validated)
            self.assertFalse(validated.exists())

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


class DiagnosticsTests(unittest.TestCase):
    def test_failed_command_names_only_program_and_subcommand(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='secret-output')):
            with self.assertRaises(module.Refusal) as caught:
                module.run(['docker', 'push', 'docker.io/acct/private:tag'])
        self.assertIn('docker push', str(caught.exception))
        self.assertNotIn('acct', str(caught.exception))
        self.assertNotIn('secret-output', str(caught.exception))


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
        self.assertIn('exited 130', module.CLEAN_EXIT)  # T3 under tini
        self.assertNotIn('exited 137', module.CLEAN_EXIT)  # killed after timeout

    def test_capture_refuses_running_crm_writers_when_disabled(self):
        module = self.module()
        def command(argv):
            if argv[:2] == ['docker', 'ps']:
                return 'abc123'
            return 'running'
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=command), \
                patch.object(module, 'disk_used_percent', return_value=50), patch.object(module, 'compose_files', return_value=[]), \
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
            if argv[:2] == ['docker', 'compose']:
                raise module.Refusal('compose failed')
            return ''
        with patch.object(module, 'run', side_effect=command):
            with self.assertRaisesRegex(module.Refusal, 'nixos-twenty-db'):
                module.start_all(list(reversed(module.CONTAINERS + module.CRM_CONTAINERS)))
        self.assertTrue(set(module.CONTAINERS) <= set(started))
        self.assertIn(module.TWENTY_DB_IMAGE, (ROOT / 'deploy/compose.yaml').read_text())
        hook = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        self.assertIn('|| rc=1', hook)
        self.assertNotIn('|| exit 1', hook)


class DeletedContainerTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_deleted_container_is_recreated_from_deployed_compose(self):
        module = self.module()
        calls, gone = [], {'nixos-t3'}
        def command(argv):
            calls.append(argv)
            if argv[:2] == ['docker', 'start'] and argv[2] in gone:
                raise module.Refusal('no such container')
            if argv[:2] == ['docker', 'compose']:
                gone.clear()
            return ''
        with patch.object(module, 'run', side_effect=command), patch.object(module, 'crm_enabled', return_value=False), \
                patch.object(module, 'compose_files', return_value=['-f', 'c.yaml', '-f', 'packs.json']):
            module.start_all(list(reversed(module.CONTAINERS)))
        compose = [c for c in calls if c[:2] == ['docker', 'compose']]
        self.assertEqual(1, len(compose))
        self.assertEqual(['up', '--detach', '--no-recreate'], compose[0][-3:])
        self.assertIn('nixos', compose[0])
        self.assertEqual(2, sum(1 for c in calls if c == ['docker', 'start', 'nixos-t3']))
        hook = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        self.assertIn('up --detach --no-recreate', hook)

    def test_capture_refuses_without_disk_headroom_and_never_stops_services(self):
        module = self.module()
        calls = []
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=lambda a: calls.append(a) or 'running'), \
                patch.object(module, 'disk_used_percent', return_value=91), patch.object(module, 'compose_files', return_value=[]), patch.object(module, 'crm_enabled', return_value=False):
            with self.assertRaisesRegex(module.Refusal, 'Disk is 91% full'):
                with module.stopped_services(Path(td) / 'lock'):
                    self.fail('must not capture')
        self.assertFalse([c for c in calls if c[:2] in (['docker', 'stop'], ['docker', 'start'])])


    def test_disk_percent_matches_df_with_reserved_blocks(self):
        module = self.module()
        # 100 blocks, 10 free of which 5 are root-reserved: df shows 90 / (90 + 5) -> 95%.
        st = SimpleNamespace(f_blocks=100, f_bfree=10, f_bavail=5, f_frsize=4096)
        with patch.object(module.os, 'statvfs', return_value=st):
            self.assertEqual(95, module.disk_used_percent('/'))
        st = SimpleNamespace(f_blocks=1000, f_bfree=160, f_bavail=110, f_frsize=4096)  # 840/950 = 88.4 -> 89
        with patch.object(module.os, 'statvfs', return_value=st):
            self.assertGreaterEqual(module.disk_used_percent('/'), module.DISK_LIMIT_PERCENT)
        if sys.platform.startswith('linux'):  # APFS shares free space across volumes
            real = subprocess.run(['df', '-P', '/'], capture_output=True, text=True).stdout.split('\n')[1].split()[4]
            self.assertLessEqual(abs(module.disk_used_percent('/') - int(real.rstrip('%'))), 1)

    def hook_run(self, td, missing, crm, packs=True):
        unit = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        line = next(l for l in unit.splitlines() if l.startswith('ExecStopPost='))
        script = line.split("/bin/sh -c '", 1)[1].rsplit("'", 1)[0].replace('$$', '$')
        flag = Path(td) / 'ENABLED'
        if crm:
            flag.write_text('')
        script = script.replace('/srv/nix-clean/twenty/ENABLED', str(flag))
        override = Path(td) / 'packs.compose.json'
        if packs:
            override.write_text('{}')
        else:
            override.unlink(missing_ok=True)
        script = script.replace('/srv/nix-clean/packs.compose.json', str(override))
        bindir, log = Path(td) / 'bin', Path(td) / 'calls'
        bindir.mkdir(exist_ok=True)
        docker = bindir / 'docker'
        docker.write_text('#!/bin/sh\necho "$*" >> "%s"\nif [ "$1" = start ]; then for m in %s; do '
                          '[ "$2" = "$m" ] && exit 1; done; fi\nexit 0\n' % (log, ' '.join(missing) or 'none'))
        docker.chmod(0o755)
        env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin')
        rc = subprocess.run(['/bin/sh', '-c', script], env=env).returncode
        return rc, log.read_text().splitlines() if log.exists() else []

    def test_stop_hook_recreates_missing_crm_container_with_crm_profile(self):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = self.hook_run(td, ['nixos-twenty-db'], crm=True)
        compose = [c for c in calls if c.startswith('compose')]
        self.assertEqual(0, rc)
        self.assertEqual(1, len(compose))
        self.assertIn('--profile crm up --detach --no-recreate', compose[0])

    def test_stop_hook_without_crm_starts_only_core_and_no_profile(self):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = self.hook_run(td, [], crm=False)
            self.assertEqual(0, rc)
            self.assertEqual(['start nixos-hindsight', 'start nixos-hermes', 'start nixos-t3', 'start nixos-reviewer'], calls)
            rc, calls = self.hook_run(td, ['nixos-t3'], crm=False)
        compose = [c for c in calls if c.startswith('compose')]
        self.assertEqual(1, len(compose))
        self.assertNotIn('--profile', compose[0])


    def test_stop_hook_recreates_with_the_generated_nixkit_override_and_refuses_without_it(self):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = self.hook_run(td, ['nixos-t3'], crm=False, packs=True)
            compose = [c for c in calls if c.startswith('compose')]
            self.assertEqual(0, rc)
            self.assertIn(f'-f /opt/nix-clean-source/deploy/compose.yaml -f {td}/packs.compose.json up', compose[0])
        with tempfile.TemporaryDirectory() as td:
            rc, calls = self.hook_run(td, ['nixos-t3'], crm=False, packs=False)
        # never recreate Hermes/T3 without their skill mounts: fail loudly instead
        self.assertNotEqual(0, rc)
        self.assertFalse([c for c in calls if c.startswith('compose')])


class NixKitOverrideTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_deployment_override_is_layered_exactly_like_the_deploy_does(self):
        module = self.module()
        deploy = (ROOT / 'scripts/deploy-os.py').read_text()
        self.assertIn("'--file', str(source / 'deploy/compose.yaml'), '--file', str(state / 'packs.compose.json')", deploy)
        with tempfile.TemporaryDirectory() as td:
            root, source = Path(td) / 'root', Path('/release')
            root.mkdir()
            with self.assertRaisesRegex(module.Refusal, 'override'):
                module.compose_files(root, source)
            (root / 'packs.compose.json').write_text('{}')
            self.assertEqual(['-f', '/release/deploy/compose.yaml', '-f', str(root / 'packs.compose.json')],
                             module.compose_files(root, source))

    def test_model_and_recreate_use_the_override_so_skill_mounts_are_not_lost_or_refused(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'root'
            root.mkdir()
            (root / 'packs.compose.json').write_text('{}')
            source = Path(td) / 'src'
            (source / 'deploy/t3').mkdir(parents=True)
            (source / 'deploy/t3/Dockerfile').write_text('FROM x\n')
            calls = []
            def command(argv, **kwargs):
                calls.append(argv)
                if argv[:3] == ['docker', 'compose', '-f'] and 'config' in argv:
                    services = {}
                    for name in ['hermes', 't3', 'reviewer']:
                        extra = [{'type': 'bind', 'source': str(root / 'pack-releases/k/skill'), 'target': '/skills/skill', 'read_only': True}] if name == 'hermes' else []
                        services[name] = {'container_name': 'nixos-' + name, 'image': 'img',
                                          'volumes': [{'type': 'bind', 'source': str(root / name), 'target': '/data'}] + extra}
                    return json.dumps({'services': services})
                name = argv[2].removeprefix('nixos-')
                mounts = [{'Type': 'bind', 'Source': str(root / name), 'Destination': '/data', 'RW': True}]
                if name == 'hermes':
                    mounts.append({'Type': 'bind', 'Source': str(root / 'pack-releases/k/skill'), 'Destination': '/skills/skill', 'RW': False})
                return json.dumps([{'Config': {'Image': 'img', 'Env': []}, 'Mounts': mounts}])
            with patch.object(module, 'run', side_effect=command), patch.object(module, 'crm_enabled', return_value=False):
                module.deployment_model(root, {'hermes': {'image': 'img'}}, source)
            compose = next(c for c in calls if c[:2] == ['docker', 'compose'])
            self.assertIn(str(root / 'packs.compose.json'), compose)
            calls.clear()
            with patch.object(module, 'run', side_effect=command), patch.object(module, 'crm_enabled', return_value=False):
                module.recreate_missing(root)
            self.assertIn(str(root / 'packs.compose.json'), calls[0])
            (root / 'packs.compose.json').unlink()
            calls.clear()
            with patch.object(module, 'run', side_effect=command), patch.object(module, 'crm_enabled', return_value=False):
                with self.assertRaisesRegex(module.Refusal, 'override'):
                    module.recreate_missing(root)
                with self.assertRaisesRegex(module.Refusal, 'override'):
                    module.deployment_model(root, {'hermes': {'image': 'img'}}, source)
            self.assertFalse([c for c in calls if c[:2] == ['docker', 'compose']])

    def test_capture_refuses_before_stopping_anything_without_the_override(self):
        module = self.module()
        calls = []
        with tempfile.TemporaryDirectory() as td, patch.object(module, 'run', side_effect=lambda a: calls.append(a) or 'running'), \
                patch.object(module, 'disk_used_percent', return_value=50), patch.object(module, 'compose_files', return_value=[]), patch.object(module, 'crm_enabled', return_value=False), \
                patch.object(module, 'compose_files', side_effect=module.Refusal('The deployment Compose override is missing')):
            with self.assertRaisesRegex(module.Refusal, 'override'):
                with module.stopped_services(Path(td) / 'lock'):
                    self.fail('must not capture')
        self.assertFalse(calls)


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


class CrmCoverageTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_create', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_ready_wait_outlasts_twenty_server_healthcheck_budget_from_compose(self):
        module = self.module()
        compose = (ROOT / 'deploy/compose.yaml').read_text()
        server = compose.split('  twenty-server:', 1)[1].split('\n  twenty-worker:', 1)[0]
        health = server.split('healthcheck:', 1)[1]
        seconds = lambda key: int(re.search(rf'{key}: (\d+)s', health).group(1))
        budget = seconds('start_period') + int(re.search(r'retries: (\d+)', health).group(1)) * seconds('interval')
        self.assertGreater(module.CRM_READY_WAIT_SECONDS, budget)
        def waited(names):
            clock = [0.0]
            with patch.object(module, 'ready', return_value=False), \
                    patch.object(module.time, 'sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)), \
                    patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
                with self.assertRaises(module.Refusal):
                    module.wait_services(names)
            return clock[0]
        self.assertAlmostEqual(module.READY_WAIT_SECONDS, waited(['nixos-hermes']), delta=3)
        self.assertAlmostEqual(module.CRM_READY_WAIT_SECONDS, waited(['nixos-hermes', 'nixos-twenty-server']), delta=3)
        self.assertGreater(module.CRM_READY_WAIT_SECONDS, module.READY_WAIT_SECONDS)

    def test_twenty_worker_has_enough_memory_to_stay_running(self):
        # A worker restarting every ~30s (Node heap OOM at 768m) makes "all services running" a coin
        # flip, so the nightly capture would refuse at random.
        compose = (ROOT / 'deploy/compose.yaml').read_text()
        limit = lambda name: int(re.search(r'mem_limit: (\d+)m', compose.split(f'  {name}:', 1)[1]).group(1))
        self.assertGreaterEqual(limit('twenty-worker'), 1024)

    def test_twenty_probe_requires_the_migrated_core_schema(self):
        module = self.module()
        with patch.object(module, 'run', return_value='1: case = "NIXOS_TWENTY_DB_EMPTY"') as run:
            with self.assertRaises(module.Refusal):
                module.twenty_db_check('img@sha256:a', Path('/scratch/state'))
        self.assertIn("schema_name = 'core'", run.call_args.kwargs['input'])
        self.assertEqual('none', run.call_args.args[0][run.call_args.args[0].index('--network') + 1])
        with patch.object(module, 'run', return_value='1: case = "NIXOS_TWENTY_DB_OK"'):
            module.twenty_db_check('img@sha256:a', Path('/scratch/state'))

    def test_deployment_model_cross_checks_crm_images_and_mounts_when_enabled(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'root'
            (root / 'twenty').mkdir(parents=True)
            (root / 'twenty/ENABLED').write_text('')
            (root / 'packs.compose.json').write_text('{}')
            services = {}
            for name in ['hermes', 't3', 'reviewer'] + module.CRM_SERVICES:
                services[name] = {'container_name': 'nixos-' + name, 'image': f'img-{name}',
                                  'volumes': [{'type': 'bind', 'source': str(root / name), 'target': '/data'}]}
            inspected = []
            def command(argv, **kwargs):
                if argv[:2] == ['docker', 'compose']:
                    self.assertEqual('img-hermes', kwargs['env']['HERMES_IMAGE'])
                    return json.dumps({'services': services})
                name = argv[2].removeprefix('nixos-')
                inspected.append(name)
                return json.dumps([{'Config': {'Image': f'img-{name}', 'Env': ['A=1']},
                                    'Mounts': [{'Type': 'bind', 'Source': str(root / name), 'Destination': '/data', 'RW': True}]}])
            versions = {'hermes': {'image': 'official-base', 'runtime_image': 'img-hermes'}}
            (Path(td) / 'src/deploy/t3').mkdir(parents=True)
            (Path(td) / 'src/deploy/t3/Dockerfile').write_text('FROM x\n')
            with patch.object(module, 'run', side_effect=command):
                model = module.deployment_model(root, versions, Path(td) / 'src')
            self.assertEqual(['hermes', 't3', 'reviewer'] + module.CRM_SERVICES, inspected)
            self.assertEqual({'A': '1'}, model['services']['twenty-db']['environment'])
            # a CRM container running another image, or with an unexpected mount, is refused
            def wrong_image(argv, **kwargs):
                out = command(argv, **kwargs)
                if argv[:3] == ['docker', 'inspect', 'nixos-twenty-db']:
                    return out.replace('img-twenty-db', 'img-other')
                return out
            with patch.object(module, 'run', side_effect=wrong_image):
                with self.assertRaisesRegex(module.Refusal, 'image pins'):
                    module.deployment_model(root, versions, Path(td) / 'src')
            def extra_mount(argv, **kwargs):
                out = command(argv, **kwargs)
                if argv[:3] == ['docker', 'inspect', 'nixos-twenty-server']:
                    row = json.loads(out)
                    row[0]['Mounts'].append({'Type': 'volume', 'Name': 'anon', 'Source': '/var/lib/docker/v', 'Destination': '/x', 'RW': True})
                    return json.dumps(row)
                return out
            with patch.object(module, 'run', side_effect=extra_mount):
                with self.assertRaisesRegex(module.Refusal, 'mounts differ'):
                    module.deployment_model(root, versions, Path(td) / 'src')
            # CRM disabled: only the core services are inspected
            (root / 'twenty/ENABLED').unlink()
            inspected.clear()
            with patch.object(module, 'run', side_effect=command):
                module.deployment_model(root, versions, Path(td) / 'src')
            self.assertEqual(['hermes', 't3', 'reviewer'], inspected)

    def test_stop_hook_and_capture_cover_reviewer_and_every_crm_container(self):
        module = self.module()
        hook = next(l for l in (ROOT / 'automation/recovery/nixos-recovery.service').read_text().splitlines()
                    if l.startswith('ExecStopPost='))
        for name in module.CONTAINERS + module.CRM_CONTAINERS:
            self.assertIn(name, hook)
        self.assertNotIn('nixos-paperclip', hook)
