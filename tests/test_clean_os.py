import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('clean_os', ROOT / 'scripts/prepare-clean-os.py')
clean_os = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_os)


class CleanPreparationTests(unittest.TestCase):
    def test_preview_never_creates_state(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            destination = Path(td) / 'deployment'
            self.assertEqual(0, clean_os.main(['--root', str(destination)]))
            self.assertFalse(destination.exists())

    def test_prepare_creates_distinct_profiles_and_never_overwrites_state(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            destination = Path(td) / 'deployment'
            clean_os.prepare(destination, roles={key: 'Synthetic role/license' for key in ('lead', 'engineer', 'reviewer', 'license')})
            home = destination / 'hermes'
            keys = [(home / rel / '.env').read_text() for rel in ('', 'profiles/engineer', 'profiles/reviewer')]
            self.assertEqual(3, len(set(keys)))
            self.assertTrue(all('API_SERVER_KEY=' in k for k in keys))
            for rel in ('', 'profiles/engineer', 'profiles/reviewer'):
                self.assertTrue((home / rel / 'SOUL.md').is_file())
                self.assertEqual(0o600, (home / rel / '.env').stat().st_mode & 0o777)
            with self.assertRaises(clean_os.Refusal):
                clean_os.prepare(destination, roles={key: 'Synthetic role/license' for key in ('lead', 'engineer', 'reviewer', 'license')})
            self.assertEqual(keys[0], (home / '.env').read_text())

    def test_only_official_image_namespaces_are_accepted(self):
        import json
        from unittest.mock import patch
        versions = json.loads((ROOT / 'deploy/versions.json').read_text())
        versions['hermes']['image'] = 'untrusted/runtime@sha256:' + 'a' * 64
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            (fixture / 'deploy').mkdir()
            (fixture / 'deploy/versions.json').write_text(json.dumps(versions))
            (fixture / 'deploy/pilot.json').write_text((ROOT / 'deploy/pilot.json').read_text())
            with patch.object(clean_os, 'REPO', fixture), self.assertRaises(clean_os.Refusal):
                clean_os.inputs()
