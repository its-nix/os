import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery_create', ROOT / 'automation/recovery/create.py')
create = importlib.util.module_from_spec(spec)
spec.loader.exec_module(create)


class CreateTests(unittest.TestCase):
    def test_preview_does_not_run_commands_or_read_state(self):
        from unittest.mock import patch
        with patch.object(create, 'run', side_effect=AssertionError('command executed')):
            self.assertEqual(0, create.main([
                '--home', '/not-present', '--hermes-source', '/not-present',
                '--runtime-image', 'example/hermes@sha256:' + 'a' * 64,
                '--image', 'ghcr.io/example/recovery',
            ]))

    def test_missing_or_symlinked_learned_skills_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            skill = home / 'skills/learned/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('learned behavior')
            with self.assertRaises(create.Refusal):
                create.check_coverage(home, {})
            create.check_coverage(home, {'skills/learned/SKILL.md': create.recovery.digest(skill)})
            skill.unlink()
            skill.symlink_to(home / 'elsewhere')
            with self.assertRaises(create.Refusal):
                create.check_coverage(home, {})

    def test_runtime_revision_mismatch_is_refused(self):
        from unittest.mock import patch
        with patch.object(create, 'run', return_value='different'):
            with self.assertRaisesRegex(create.Refusal, 'revision'):
                create.check_runtime('example@sha256:' + 'a' * 64, 'b' * 40)

    def test_push_happens_only_after_restore_check_and_private_preflight(self):
        from unittest.mock import patch
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['gh', 'api']:
                return '{"visibility": "private"}'
            if argv[:3] == ['gh', 'auth', 'token']:
                return 'synthetic-login-value'
            return ''
        with tempfile.TemporaryDirectory() as td, patch.object(create, 'run', side_effect=command):
            create.verify_and_publish('ghcr.io/example/recovery:dated', Path(td), True)
        canary = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'run'])
        push = next(i for i, c in enumerate(calls) if 'push' in c)
        self.assertLess(canary, push)
        self.assertIn('--network', calls[canary])
        self.assertIn('none', calls[canary])
        calls.clear()
        with tempfile.TemporaryDirectory() as td, patch.object(create, 'run', side_effect=RuntimeError('restore failed')):
            with self.assertRaises(RuntimeError):
                create.verify_and_publish('ghcr.io/example/recovery:dated', Path(td), True)
        self.assertFalse(any('push' in c for c in calls))

    def test_public_or_inaccessible_registry_is_refused_before_push(self):
        from unittest.mock import patch
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            return '{"visibility": "public"}' if argv[:2] == ['gh', 'api'] else ''
        with tempfile.TemporaryDirectory() as td, patch.object(create, 'run', side_effect=command):
            with self.assertRaises(create.Refusal):
                create.verify_and_publish('ghcr.io/example/recovery:dated', Path(td), True)
        self.assertFalse(any('push' in c for c in calls))

    def test_failed_backup_cannot_build_or_publish(self):
        from argparse import Namespace
        from unittest.mock import patch
        calls = []
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / 'config.yaml').write_text('example: true')
            args = Namespace(home=home, hermes_source=home, hermes_bin='hermes',
                             runtime_image='pinned', image='ghcr.io/example/recovery', publish=True)
            def command(argv, **kwargs):
                calls.append(argv)
                if 'status' in argv:
                    return ''
                if 'rev-parse' in argv:
                    return 'b' * 40
                if '--version' in argv:
                    return 'Hermes upstream ' + 'b' * 8
                if 'backup' in argv:
                    raise create.Refusal('incomplete backup')
                return ''
            with patch.object(create, 'run', side_effect=command), patch.object(create, 'check_runtime'), patch.object(create, 'require_private'):
                with self.assertRaises(create.Refusal):
                    create.build(args)
        self.assertFalse(any('build' in c or 'push' in c for c in calls))
