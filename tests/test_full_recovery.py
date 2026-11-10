from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FullRecoveryTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('full_restore', ROOT / 'automation/recovery/full_restore.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_unsafe_links_and_archive_paths_refused(self):
        recovery = self.module()
        for name in ('../outside', '/absolute', 'hermes/../outside', 'hermes//bad', 'hermes/./bad'):
            with self.subTest(name=name), self.assertRaises(recovery.Refusal):
                recovery.safe_name(name)
        for name, target in [('hermes/link', '../../outside'), ('hermes/link', '/etc/passwd'),
                             ('hermes/nix/kb/.venv/bin/python', '/opt/hermes/../../etc/passwd')]:
            with self.subTest(target=target), self.assertRaises(recovery.Refusal):
                recovery.safe_link(name, target)
        recovery.safe_link('hermes/nix/kb/.venv/bin/python', '/opt/hermes/tools/python/bin/python')

    def test_missing_state_or_key_material_refused(self):
        recovery = self.module()
        with self.assertRaises(recovery.Refusal):
            recovery.require_state({})
        with self.assertRaises(recovery.Refusal):
            recovery.require_state({'hermes/config.yaml': {'type': 'file'},
                                    'paperclip/instances/default/db/PG_VERSION': {'type': 'file'}})

    def test_complete_restore_preserves_learning_and_controller_state(self):
        path = ROOT / 'automation/recovery/full_restore.py'
        self.assertTrue(path.exists(), 'Full-system recovery implementation is missing')
        spec = importlib.util.spec_from_file_location('full_restore', path)
        recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recovery)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'live'
            for name, content in {
                'hermes/config.yaml': 'model: example',
                'hermes/memories/MEMORY.md': 'durable learned context',
                'hermes/profiles/engineer/skills/learned/SKILL.md': '# Learned procedure',
                'paperclip/instances/default/db/PG_VERSION': '18',
                'paperclip/instances/default/secrets/master.key': 'synthetic-key',
                'knowledge/company/START-HERE.md': '# Company knowledge',
            }.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            with closing(sqlite3.connect(root / 'hermes/state.db')) as conn:
                conn.execute('CREATE TABLE memories (body TEXT)')
                conn.execute('INSERT INTO memories VALUES (?)', ('learned result',))
                conn.commit()
            archive = Path(td) / 'snapshot.tar.gz'
            inventory = recovery.snapshot(root, archive)
            manifest = {'archive_sha256': recovery.digest(archive), 'files': inventory}
            restored = Path(td) / 'restored'
            recovery.restore(archive, manifest, restored, preserve_owner=False)
            self.assertEqual('durable learned context', (restored / 'hermes/memories/MEMORY.md').read_text())
            self.assertEqual('synthetic-key', (restored / 'paperclip/instances/default/secrets/master.key').read_text())
            with closing(sqlite3.connect(restored / 'hermes/state.db')) as conn:
                self.assertEqual([('learned result',)], conn.execute('SELECT body FROM memories').fetchall())
            self.assertTrue((restored / 'hermes/profiles/engineer/skills/learned/SKILL.md').is_file())
            with self.assertRaises(recovery.Refusal):
                recovery.restore(archive, manifest, restored, preserve_owner=False)
            # Both outer corruption and an internally inconsistent inventory must fail.
            bad_manifest = dict(manifest, archive_sha256='0' * 64)
            with self.assertRaises(recovery.Refusal):
                recovery.restore(archive, bad_manifest, Path(td) / 'bad-hash', preserve_owner=False)
            inventory['hermes/memories/MEMORY.md']['sha256'] = '0' * 64
            with self.assertRaises(recovery.Refusal):
                recovery.restore(archive, manifest, Path(td) / 'bad-file', preserve_owner=False)
            del inventory['hermes/memories/MEMORY.md']
            with self.assertRaises(recovery.Refusal):
                recovery.restore(archive, manifest, Path(td) / 'bad-coverage', preserve_owner=False)
