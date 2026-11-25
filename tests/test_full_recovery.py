from contextlib import closing
import importlib.util
import json
import os
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
        # Hermes-managed install environments link into the same pinned runtime.
        for name in ('hermes/installs/351f81cc/environments/20ce4c50/venv/bin/python',
                     'hermes/profiles/engineer/installs/ab12/environments/cd34/venv/bin/python3'):
            recovery.safe_link(name, '/opt/hermes/tools/python-3.14.9/bin/python3')
        for name, target in [('hermes/installs/x/environments/y/venv/bin/python', '/usr/bin/python3'),
                             ('hermes/installs/x/environments/y/venv/lib/evil', '/opt/hermes/tools/python'),
                             ('hermes/installs/x/environments/y/venv/bin/sub/python', '/opt/hermes/x'),
                             ('hermes/workspace/installs/x/environments/y/venv/bin/python', '/opt/hermes/x'),
                             ('paperclip/installs/x/environments/y/venv/bin/python', '/opt/hermes/x')]:
            with self.subTest(name=name, target=target), self.assertRaises(recovery.Refusal):
                recovery.safe_link(name, target)

    def test_restore_refuses_archived_link_outside_runtime(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'live'
            for name in ('hermes/config.yaml', 'paperclip/instances/default/db/PG_VERSION',
                         'paperclip/instances/default/secrets/master.key'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('x')
            link = root / 'hermes/installs/a/environments/b/venv/bin/python'
            link.parent.mkdir(parents=True)
            link.symlink_to('/opt/hermes/tools/python3')
            archive = Path(td) / 'snapshot.tar.gz'
            inventory = recovery.snapshot(root, archive)
            # Tamper: the archive+manifest now claim an escaping target.
            import tarfile
            tampered = Path(td) / 'tampered.tar.gz'
            with tarfile.open(archive) as src, tarfile.open(tampered, 'w:gz') as dst:
                for member in src.getmembers():
                    if member.issym():
                        member.linkname = '/etc/shadow'
                    dst.addfile(member, src.extractfile(member) if member.isfile() else None)
            inventory['hermes/installs/a/environments/b/venv/bin/python']['target'] = '/etc/shadow'
            manifest = {'archive_sha256': recovery.digest(tampered), 'files': inventory}
            with self.assertRaises(recovery.Refusal):
                recovery.restore(tampered, manifest, Path(td) / 'restored', preserve_owner=False)
            self.assertFalse((Path(td) / 'restored').exists())

    def test_rebuildable_t3_worktrees_and_caches_are_not_captured(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ('t3/home/.t3/userdata/state.json', 't3/home/.codex/auth.json',
                         't3/work/repo/node_modules/x/index.js', 't3/home/.cache/big.bin',
                         't3/home/.codex/tmp/arg0/codex', 't3/home/.t3/worktrees/os/t_1/venv/x'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('x')
            (root / 't3/work/repo/node_modules/.bin').mkdir()
            (root / 't3/work/repo/node_modules/.bin/tool').symlink_to('/usr/bin/env')  # would be refused
            rels = {p.relative_to(root).as_posix() for p in recovery.durable_paths(root)}
            self.assertIn('t3/home/.t3/userdata/state.json', rels)
            self.assertIn('t3/home/.codex/auth.json', rels)
            self.assertIn('t3/work', rels)
            self.assertFalse(any(r.startswith(('t3/work/', 't3/home/.cache/', 't3/home/.codex/tmp/', 't3/home/.t3/worktrees/')) for r in rels))

    def test_unit_stop_hook_restarts_every_service(self):
        unit = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        create = (ROOT / 'automation/recovery/full_create.py').read_text()
        hook = next(l for l in unit.splitlines() if l.startswith('ExecStopPost='))
        for name in ('nixos-hermes', 'nixos-t3'):
            self.assertIn(name, hook)
            self.assertIn(repr(name), create)
        self.assertNotIn('nixos-paperclip', hook)  # retired: never restarted by recovery

    def test_snapshot_without_paperclip_is_valid_but_partial_archive_is_not(self):
        recovery = self.module()
        recovery.require_state({'hermes': {'type': 'directory'}, 'hermes/config.yaml': {'type': 'file'}})
        with self.assertRaises(recovery.Refusal):
            recovery.require_state({'hermes': {'type': 'directory'}, 'hermes/config.yaml': {'type': 'file'},
                                    'paperclip': {'type': 'directory'},
                                    'paperclip/instances': {'type': 'directory'},
                                    'paperclip/instances/default': {'type': 'directory'},
                                    'paperclip/instances/default/db': {'type': 'directory'},
                                    'paperclip/instances/default/db/PG_VERSION': {'type': 'file'}})

    def test_missing_state_or_key_material_refused(self):
        recovery = self.module()
        with self.assertRaises(recovery.Refusal):
            recovery.require_state({})
        with self.assertRaises(recovery.Refusal):
            recovery.require_state({'hermes/config.yaml': {'type': 'file'},
                                    'paperclip/instances/default/db/PG_VERSION': {'type': 'file'}})

    def test_twenty_flag_database_and_keys_must_travel_together(self):
        recovery = self.module()
        base = {'hermes': {'type': 'directory'}, 'hermes/config.yaml': {'type': 'file'}, 'twenty': {'type': 'directory'},
                'twenty/db': {'type': 'directory'}}
        full = dict(base, **{'twenty/ENABLED': {'type': 'file', 'size': 0},
                             'twenty/db/PG_VERSION': {'type': 'file', 'size': 3},
                             'twenty/db.env': {'type': 'file', 'size': 90},
                             'twenty/server.env': {'type': 'file', 'size': 200}})
        recovery.require_state(full)
        recovery.require_state(base)  # CRM never enabled: nothing required
        for missing in ('twenty/db/PG_VERSION', 'twenty/db.env', 'twenty/server.env'):
            partial = {k: v for k, v in full.items() if k != missing}
            with self.assertRaises(recovery.Refusal, msg=missing):
                recovery.require_state(partial)
        with self.assertRaises(recovery.Refusal):  # empty key file
            recovery.require_state(dict(full, **{'twenty/server.env': {'type': 'file', 'size': 0}}))
        flag_only = {k: v for k, v in full.items() if k != 'twenty/db/PG_VERSION'}
        with self.assertRaises(recovery.Refusal):
            recovery.require_state(flag_only)
        for missing in ('twenty/ENABLED',):  # database and keys without the flag would restore a disabled CRM
            with self.assertRaises(recovery.Refusal, msg=missing):
                recovery.require_state({k: v for k, v in full.items() if k != missing})

    def test_reviewer_logins_and_app_key_are_captured_but_its_caches_are_not(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ('reviewer/secrets/app.pem', 'reviewer/secrets/seed', 'reviewer/reviewer.env',
                         'reviewer/home/.claude/.credentials.json', 'reviewer/home/.codex/auth.json',
                         'reviewer/home/.local/share/opencode/auth.json', 'reviewer/home/.grok/auth.json',
                         'reviewer/home/.gemini/antigravity-cli/state.json',
                         'reviewer/home/.npm/_cacache/x', 'reviewer/home/.cache/y', 'reviewer/home/.codex/tmp/z'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('x')
            (root / 'reviewer/secrets/app.pem').chmod(0o400)
            rels = {p.relative_to(root).as_posix() for p in recovery.durable_paths(root)}
        for kept in ('reviewer/secrets/app.pem', 'reviewer/secrets/seed', 'reviewer/reviewer.env',
                     'reviewer/home/.claude/.credentials.json', 'reviewer/home/.codex/auth.json',
                     'reviewer/home/.local/share/opencode/auth.json', 'reviewer/home/.grok/auth.json',
                     'reviewer/home/.gemini/antigravity-cli/state.json'):
            self.assertIn(kept, rels)
        self.assertFalse(any(r.startswith(('reviewer/home/.npm/', 'reviewer/home/.cache/',
                                           'reviewer/home/.codex/tmp/')) for r in rels))

    def test_twenty_database_redis_storage_and_env_are_captured_cold_state(self):
        recovery = self.module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ('twenty/ENABLED', 'twenty/db.env', 'twenty/server.env', 'twenty/db/PG_VERSION',
                         'twenty/db/base/1/1259', 'twenty/db/postmaster.pid', 'twenty/redis/appendonlydir/a.aof',
                         'twenty/storage/attachment/f.bin'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('x')
            rels = {p.relative_to(root).as_posix() for p in recovery.durable_paths(root)}
            self.assertTrue({'twenty/ENABLED', 'twenty/db.env', 'twenty/server.env', 'twenty/db/PG_VERSION',
                             'twenty/db/base/1/1259', 'twenty/redis/appendonlydir/a.aof',
                             'twenty/storage/attachment/f.bin'} <= rels)
        # a live PostgreSQL pid file must never be revived on a restored host
        self.assertIn('postmaster.pid', recovery.INERT)

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
                'paperclip/instances/default/locks/directory-merge/' + 'a' * 64 + '.lock.sqlite': '',
                't3/home/.t3/userdata/server-owner.sqlite': '',
                'knowledge/company/START-HERE.md': '# Company knowledge',
            }.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            managed = root / 'hermes/installs/351f81cc/environments/20ce4c50/venv/bin/python'
            managed.parent.mkdir(parents=True)
            managed.symlink_to('/opt/hermes/tools/python-3.14.9/bin/python3')
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
            lock = restored / 'paperclip/instances/default/locks/directory-merge' / ('a' * 64 + '.lock.sqlite')
            self.assertEqual(0, lock.stat().st_size)
            self.assertEqual(0, (restored / 't3/home/.t3/userdata/server-owner.sqlite').stat().st_size)
            with closing(sqlite3.connect(restored / 'hermes/state.db')) as conn:
                self.assertEqual([('learned result',)], conn.execute('SELECT body FROM memories').fetchall())
            self.assertTrue((restored / 'hermes/profiles/engineer/skills/learned/SKILL.md').is_file())
            self.assertEqual('/opt/hermes/tools/python-3.14.9/bin/python3',
                             os.readlink(restored / 'hermes/installs/351f81cc/environments/20ce4c50/venv/bin/python'))
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
