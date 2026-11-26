import configparser
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vault', ROOT / 'deploy/drive/vault.py')
vault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vault)


class VaultTests(unittest.TestCase):
    def test_config_scopes_one_remote_and_blocks_shortcuts_and_exports(self):
        cfg = configparser.RawConfigParser()
        token = dict(access_token='test', refresh_token='test')
        cfg.read_string(vault.config_text('fixture', token))
        self.assertEqual(cfg.sections(), ['nix'])
        self.assertEqual(cfg['nix']['scope'], 'drive')
        self.assertEqual(cfg['nix']['root_folder_id'], 'fixture')
        self.assertTrue(cfg['nix'].getboolean('skip_shortcuts'))
        self.assertTrue(cfg['nix'].getboolean('skip_gdocs'))
        for bad in ('', 'root', 'x\n[other]', 'some/path'):
            with self.assertRaises(ValueError):
                vault.config_text(bad, token)

    def test_mounts_exclude_private_state_and_ancestors(self):
        import re
        compose = (ROOT / 'deploy/compose.yaml').read_text()
        for service in ('hermes', 't3', 'reviewer'):
            section = re.search(rf'^  {service}:\n(.*?)(?=^  [\w-]+:|\Z)', compose, re.M | re.S)[1]
            mounts = re.findall(r'^        source: (.+)$', section, re.M)
            self.assertTrue(mounts)
            for source in mounts:
                self.assertRegex(source, r'^\$\{NIX_CLEAN_ROOT:[^}]+\}/[\w(]')
                path = Path(source.split('}/', 1)[1])
                self.assertNotIn('..', path.parts)
                self.assertNotEqual(path.parts[0], 'drive')
            self.assertNotIn('rclone.conf', section)
            self.assertNotIn('docker.sock', section)

    @unittest.skipUnless(shutil.which('rclone'), 'requires rclone CLI')
    def test_bisync_refuses_missing_sentinel_and_mass_deletion(self):
        for loss in ('sentinel', 'delete'):
            with self.subTest(loss=loss), tempfile.TemporaryDirectory() as tmp:
                left, right, history = (Path(tmp) / name for name in ('local', 'remote', 'state'))
                for path in (left, right):
                    path.mkdir()
                    (path / vault.MARK).touch()
                for i in range(10):
                    (left / str(i)).write_text(str(i))
                args = ['rclone', '--config', '/dev/null', *vault.bisync_args(left, str(right), history)]
                subprocess.run(args + ['--resync'], capture_output=True, check=True)
                removed = [vault.MARK] if loss == 'sentinel' else ['0', '1', '2']
                for name in removed:
                    (left / name).unlink()
                failed = subprocess.run(args, capture_output=True)
                self.assertNotEqual(0, failed.returncode)
                for name in removed:
                    self.assertTrue((right / name).exists())
