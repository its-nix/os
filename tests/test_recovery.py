import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'automation/recovery/recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def test_full_state_round_trip_is_inert(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / 'sample.db'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('create table messages (text)')
                conn.execute('insert into messages values (?)', ('remember this',))
                conn.commit()
            archive = root / 'snapshot.zip'
            files = {
                'config.yaml': b'model: example\n',
                'memories/MEMORY.md': b'learned context',
                'sessions/one.json': b'{"messages": ["hello"]}',
                'skills/learned/SKILL.md': b'# Learned skill',
                'profiles/project/SOUL.md': b'Project role',
                'cron/jobs.json': b'{"jobs": []}',
                '_external/.provider/settings.json': b'{"example": true}',
                'gateway_state.json': b'stale process state',
            }
            with zipfile.ZipFile(archive, 'w') as zf:
                for name, data in files.items():
                    zf.writestr(name, data)
                zf.write(db, 'state.db')
            destination = root / 'restored'
            count = recovery.restore(archive, destination)
            self.assertEqual(len(files), count)  # DB added, stale marker excluded
            for name, data in files.items():
                if name == 'gateway_state.json':
                    self.assertFalse((destination / 'hermes' / name).exists())
                    continue
                target = (destination / 'user' / name[len('_external/'):]
                          if name.startswith('_external/') else destination / 'hermes' / name)
                self.assertEqual(data, target.read_bytes())
                self.assertEqual(0o600, target.stat().st_mode & 0o777)
            with closing(sqlite3.connect(destination / 'hermes/state.db')) as conn:
                self.assertEqual([('remember this',)], conn.execute('select * from messages').fetchall())

    def test_unsafe_archives_never_create_destination(self):
        import stat
        for bad in ('../outside', '/outside', 'a/../../outside', 'a\\outside', 'a//b', 'a/./b'):
            with self.subTest(member=bad), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                archive = root / 'input.zip'
                with zipfile.ZipFile(archive, 'w') as zf:
                    zf.writestr('config.yaml', 'example: true')
                    zf.writestr(bad, 'private')
                with self.assertRaises(recovery.Refusal):
                    recovery.restore(archive, root / 'output')
                self.assertFalse((root / 'output').exists())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / 'input.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('config.yaml', 'example: true')
                link = zipfile.ZipInfo('skills/link')
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(link, '/outside')
            with self.assertRaises(recovery.Refusal):
                recovery.restore(archive, root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_invalid_database_and_nonempty_destination_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / 'input.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('config.yaml', 'example: true')
                zf.writestr('state.db', b'not a database')
            with self.assertRaisesRegex(recovery.Refusal, 'integrity'):
                recovery.restore(archive, root / 'output')
            self.assertFalse((root / 'output').exists())
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('config.yaml', 'example: true')
            output = root / 'output'
            output.mkdir()
            (output / 'existing').write_text('untouched')
            with self.assertRaisesRegex(recovery.Refusal, 'empty'):
                recovery.restore(archive, output)
            self.assertEqual('untouched', (output / 'existing').read_text())
