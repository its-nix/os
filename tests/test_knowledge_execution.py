"""All evidence below is SYNTHETIC; only temporary trees may be mutated."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

def load():
    spec = importlib.util.spec_from_file_location('knowledge_execution', ROOT / 'scripts/knowledge_execution.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class ExecutionTests(unittest.TestCase):
    def fixture(self, m, base):
        home = base / '.hermes'
        root = home / 'skills/watch'
        (root / 'scripts/__pycache__').mkdir(parents=True)
        (root / 'SKILL.md').write_text('---\nname: watch\n---\n')
        for name in m.CACHE_NAMES:
            (root / 'scripts/__pycache__' / name).write_bytes(name.encode())
        p = copy.deepcopy(m.load_packet())
        rows = m.snapshot(root)[0]
        p['cache']['inventory'] = rows
        p['cache']['files'] = [r for r in rows if r['path'].endswith('.pyc')]
        p['cache']['before_tree_sha256'] = m.installer._skill_tree_digest(root)
        removed = {r['path'] for r in p['cache']['files']} | {'scripts/__pycache__'}
        p['acquired_target_closures']['watch'] = [r for r in rows if r['path'] not in removed]
        # Compute synthetic post hash without touching the production tree.
        for name in m.CACHE_NAMES:
            (root / 'scripts/__pycache__' / name).unlink()
        (root / 'scripts/__pycache__').rmdir()
        p['cache']['after_tree_sha256'] = m.installer._skill_tree_digest(root)
        (root / 'scripts/__pycache__').mkdir()
        for name in m.CACHE_NAMES:
            (root / 'scripts/__pycache__' / name).write_bytes(name.encode())
        return home, p

    def git_fixture(self, m, base):
        """Synthetic local Git history/evidence; NEVER an approval of production."""
        import shutil
        from datetime import datetime, timezone
        for relative in (m.PACKET, m.SINGLE, Path('skills/packs.json'),
                         Path('scripts/install-skills.py'), Path('scripts/knowledge_execution.py')):
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(base), *args], stderr=subprocess.PIPE, text=True).strip()
        git('init', '-q')
        git('config', 'user.email', 'synthetic-test@example.invalid')
        git('config', 'user.name', 'SYNTHETIC TEST ONLY')
        git('add', '.')
        git('commit', '-qm', 'SYNTHETIC ancestor fixture')
        m.ANCESTOR = git('rev-parse', 'HEAD')
        m.QUOTE = 'approve lnx-knowledge-v2 at ' + m.ANCESTOR
        m.ROOT = base
        m.installer.ROOT = base
        epoch_ms = 1793059200000  # explicitly synthetic fixture timestamp
        timestamp = datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()
        m.EXPECTED_EVIDENCE = {'kind': 'approval', 'platform': 'discord',
            'author_id': '454290355320520714', 'channel_id': '1563201690663125142',
            'message_id': str((epoch_ms - 1420070400000) << 22),
            'quote': m.QUOTE, 'timestamp': timestamp}
        p = m.load_packet()
        pending = json.loads((base / 'skills/packs.json').read_text())
        approved = m.approved_manifest(pending, p)
        (base / 'skills/packs.json').write_text(json.dumps(approved))
        receipt = base / m.RECEIPT
        receipt.parent.mkdir()
        receipt.write_text(json.dumps(m.receipt_contract(p)))
        single_receipt = base / 'skills/approvals/lnx-nix-knowledge-1182c51c.json'
        single_receipt.parent.mkdir(exist_ok=True)
        single_receipt.write_text(json.dumps(m.single_receipt_contract(p)))
        git('add', '.')
        git('commit', '-qm', 'SYNTHETIC receipt fixture, not human authority')
        return p, approved, git

    def test_committed_synthetic_receipt_consumer_positive_and_scope_matrix(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p, approved, git = self.git_fixture(m, base)
            self.assertEqual(p, m.consume_receipt(approved))
            good = json.loads((base / m.RECEIPT).read_text())
            head = git('rev-parse', 'HEAD')
            mutations = [('schema_version', 1), ('profile', 'pulse'), ('hermes_profile', 'pulse'),
                ('proposal_git_commit', 'a' * 40), ('proposal_file_sha256', '0' * 64),
                ('skill_proposal_sha256', '0' * 64), ('manifest_sha256', '0' * 64),
                ('install_targets', ['nix-knowledge']), ('preservation', p['preservation'][:3]),
                ('cache_plan_sha256', '0' * 64), ('source', {}), ('nixkit', {}),
                ('approval_evidence', {**m.EXPECTED_EVIDENCE, 'author_id': '111111111111111111'}),
                ('approval_evidence', {**m.EXPECTED_EVIDENCE, 'message_id': '111111111111111111'}),
                ('approval_evidence', {**m.EXPECTED_EVIDENCE, 'channel_id': '1563181903253737542'}),
                ('approval_evidence', {**m.EXPECTED_EVIDENCE, 'timestamp': '2026-10-01T00:00:00Z'})]
            for key, value in mutations:
                with self.subTest(field=key, value=value):
                    bad = copy.deepcopy(good)
                    bad[key] = value
                    (base / m.RECEIPT).write_text(json.dumps(bad))
                    git('add', '.')
                    git('commit', '-qm', 'SYNTHETIC misbound receipt')
                    with self.assertRaises(m.Refusal):
                        m.consume_receipt(approved)
                    git('reset', '--hard', head)
            with mock.patch.object(m, 'EXPECTED_EVIDENCE', None):
                with self.assertRaisesRegex(m.Refusal, 'genuine.*evidence'):
                    m.consume_receipt(approved)

    def test_dirty_untracked_orphan_wrong_ancestor_and_manifest_refuse(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p, approved, git = self.git_fixture(m, base)
            (base / 'untracked').write_text('SYNTHETIC')
            with self.assertRaisesRegex(m.Refusal, 'clean'):
                m.consume_receipt(approved)
            (base / 'untracked').unlink()
            (base / m.RECEIPT).write_text('{}')
            with self.assertRaisesRegex(m.Refusal, 'clean'):
                m.consume_receipt(approved)
            git('reset', '--hard', 'HEAD')
            orphan = base / m.RECEIPT.parent / 'orphan.json'
            orphan.write_text('{}')
            git('add', '.')
            git('commit', '-qm', 'SYNTHETIC orphan')
            with self.assertRaisesRegex(m.Refusal, 'orphan'):
                m.consume_receipt(approved)
            git('reset', '--hard', 'HEAD~1')
            with mock.patch.object(m, 'ANCESTOR', 'a' * 40):
                with self.assertRaises(m.Refusal):
                    m.consume_receipt(approved)
            bad = copy.deepcopy(approved)
            bad['profiles']['pulse'] = []
            with self.assertRaisesRegex(m.Refusal, 'lifecycle'):
                m.consume_receipt(bad)

    def test_context_bound_voice_is_not_normalized_into_human_text(self):
        m = load()
        evidence = m.voice_decision_contract()
        self.assertEqual('', evidence['human_voice']['content'])
        self.assertNotIn('quote', evidence['human_voice'])
        self.assertFalse(evidence['human_written_text_quote'])
        self.assertFalse(evidence['voice_payload_cryptographically_verified'])
        with self.assertRaisesRegex(m.Refusal, 'genuine.*evidence'):
            m.validate_evidence(evidence)
        with mock.patch.object(m, 'EXPECTED_EVIDENCE', evidence):
            m.validate_evidence(evidence)
            for field in ('prompt', 'human_voice', 'bot_stt', 'scope'):
                bad = copy.deepcopy(evidence)
                bad[field] = {}
                with self.assertRaises(m.Refusal):
                    m.validate_evidence(bad)
            bad = copy.deepcopy(evidence)
            bad['human_voice']['quote'] = m.QUOTE
            with self.assertRaises(m.Refusal):
                m.validate_evidence(bad)
            # No changes to v1 pack channel/phrase rules.
            with self.assertRaises(m.Refusal):
                m.installer._validate_pack_approval_evidence(evidence, 'lnx-product-development-92338765', m.ANCESTOR)

    def test_inactive_authority_cannot_be_replaced_by_same_quote(self):
        m = load()
        with self.assertRaisesRegex(m.Refusal, 'genuine.*evidence'):
            m.validate_evidence({'quote': m.QUOTE})

    def test_manifest_transformation_is_only_preservation(self):
        m = load()
        pending = json.loads((ROOT / 'skills/packs.json').read_text())
        approved = m.approved_manifest(pending, m.load_packet())
        self.assertEqual(5, len(approved['host_native_satisfaction']['lnx']))
        approved['host_native_satisfaction'] = pending['host_native_satisfaction']
        self.assertEqual(pending, approved)

    def test_preview_is_pure(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            before = m.snapshot(home / 'skills/watch')[0]
            m.cache_preview(home, p)
            self.assertEqual(before, m.snapshot(home / 'skills/watch')[0])
            self.assertFalse((base / 'state').exists())

    def test_roundtrip_and_restart(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            before = m.snapshot(home / 'skills/watch')[0]
            with m.CacheTransaction(home, base / 'state', p) as tx:
                tx.remove()
                journal = tx.directory
                self.assertEqual(p['acquired_target_closures']['watch'], m.snapshot(home / 'skills/watch')[0])
            with m.CacheTransaction(home, base / 'state', p, recovery=journal) as tx:
                tx.rollback()
                tx.rollback()
            self.assertEqual(before, m.snapshot(home / 'skills/watch')[0])

    def test_completed_and_rolled_back_archives_survive_new_transaction(self):
        m = load()
        for status in ('completed', 'rolled_back'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                home, p = self.fixture(m, base)
                with m.CacheTransaction(home, base / 'state', p) as tx:
                    tx.remove()
                    if status == 'rolled_back':
                        tx.rollback()
                    else:
                        tx.journal['state'] = status
                        tx._save()
                    retained = tx.directory
                archive_before = m.snapshot(retained)[0]
                with m.CacheTransaction(home, base / 'state', p) as next_tx:
                    self.assertNotEqual(retained, next_tx.directory)
                    self.assertEqual(archive_before, m.snapshot(retained)[0])
                    self.assertEqual({retained, next_tx.directory}, set((base / 'state').iterdir()))
                self.assertEqual(archive_before, m.snapshot(retained)[0])
                self.assertEqual({'journal.json', *m.CACHE_NAMES}, {q.name for q in retained.iterdir()})

    def test_rollback_does_not_replace_concurrent_cache(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            with m.CacheTransaction(home, base / 'state', p) as tx:
                tx.remove()
                cache = home / 'skills/watch/scripts/__pycache__'
                cache.mkdir()
                target = cache / m.CACHE_NAMES[0]
                target.write_bytes(b'concurrent')
                with self.assertRaisesRegex(m.Refusal, 'manual recovery'):
                    tx.rollback()
                self.assertEqual(b'concurrent', target.read_bytes())
                self.assertTrue((tx.directory / 'journal.json').exists())

    def test_drift_special_and_symlink_fail_before_archive(self):
        m = load()
        for attack in ('extra', 'mode', 'fifo', 'symlink', 'ancestor'):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                home, p = self.fixture(m, base)
                root = home / 'skills/watch'
                target = root / 'scripts/__pycache__' / m.CACHE_NAMES[0]
                if attack == 'extra':
                    (root / 'ignored.pyc').write_bytes(b'ignored')
                elif attack == 'mode':
                    target.chmod(0o700)
                elif attack in ('fifo', 'symlink'):
                    target.unlink()
                    if attack == 'fifo': os.mkfifo(target)
                    else: target.symlink_to(root / 'SKILL.md')
                else:
                    real = base / 'real'
                    home.rename(real)
                    home.symlink_to(real, target_is_directory=True)
                with self.assertRaises((m.Refusal, OSError)):
                    m.cache_preview(home, p)
                self.assertFalse((base / 'state').exists())

    def test_inflight_identical_replacement_is_not_removed(self):
        m = load()
        for phase in ('before_archive_read', 'before_unlink'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                home, p = self.fixture(m, base)
                target = home / 'skills/watch/scripts/__pycache__' / m.CACHE_NAMES[0]
                with m.CacheTransaction(home, base / 'state', p) as tx:
                    def replace():
                        other = target.with_name('replacement')
                        other.write_bytes(target.read_bytes())
                        other.chmod(target.stat().st_mode)
                        other.replace(target)
                    if phase == 'before_archive_read':
                        real_save = tx._save
                        def save():
                            real_save()
                            if tx.journal['state'] == 'archiving': replace()
                        guard = mock.patch.object(tx, '_save', side_effect=save)
                    else:
                        real_expect = tx._expect
                        def expect(removed):
                            real_expect(removed)
                            if not removed: replace()
                        guard = mock.patch.object(tx, '_expect', side_effect=expect)
                    with guard, self.assertRaisesRegex(m.Refusal, 'identity'):
                        tx.remove()
                    self.assertEqual(m.CACHE_NAMES[0].encode(), target.read_bytes())

    def test_interrupted_after_unlink_recovers_from_durable_journal(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            original = m.snapshot(home / 'skills/watch')[0]
            real_unlink = os.unlink
            with m.CacheTransaction(home, base / 'state', p) as tx:
                def unlink(name, *args, **kwargs):
                    real_unlink(name, *args, **kwargs)
                    if name == m.CACHE_NAMES[0]:
                        raise OSError('SYNTHETIC interruption after unlink')
                with mock.patch.object(m.os, 'unlink', side_effect=unlink), self.assertRaises(OSError):
                    tx.remove()
                transaction = tx.directory
            with m.CacheTransaction(home, base / 'state', p, recovery=transaction) as tx:
                tx.rollback()
            self.assertEqual(original, m.snapshot(home / 'skills/watch')[0])

    def test_rollback_no_replace_atomic_publication_race(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            with m.CacheTransaction(home, base / 'state', p) as tx:
                tx.remove()
                real_link = os.link
                def link(src, dst, *args, **kwargs):
                    if dst == m.CACHE_NAMES[0]:
                        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs['dst_dir_fd'])
                        os.write(fd, b'concurrent')
                        os.close(fd)
                    return real_link(src, dst, *args, **kwargs)
                with mock.patch.object(m.os, 'link', side_effect=link), self.assertRaisesRegex(m.Refusal, 'manual recovery'):
                    tx.rollback()
                self.assertEqual(b'concurrent', (home / 'skills/watch/scripts/__pycache__' / m.CACHE_NAMES[0]).read_bytes())

    def test_unknown_journal_file_and_recovery_path_fail_closed(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            with m.CacheTransaction(home, base / 'state', p) as tx:
                tx.remove()
                transaction = tx.directory
            journal = json.loads((transaction / 'journal.json').read_text())
            journal['files']['../unowned'] = journal['files'][m.CACHE_NAMES[0]]
            (transaction / 'journal.json').write_text(json.dumps(journal))
            with self.assertRaisesRegex(m.Refusal, 'schema'):
                with m.CacheTransaction(home, base / 'state', p, recovery=transaction):
                    self.fail('unreachable')
            with self.assertRaises(m.Refusal):
                with m.CacheTransaction(home, base / 'state', p, recovery=base / 'other'):
                    self.fail('unreachable')

    def test_cooperating_writer_and_incomplete_transaction_guard(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            with m.CacheTransaction(home, base / 'state', p):
                with self.assertRaises(BlockingIOError):
                    with m.CacheTransaction(home, base / 'state', p):
                        self.fail('unreachable')
            with self.assertRaisesRegex(m.Refusal, 'explicit.*recovery'):
                with m.CacheTransaction(home, base / 'state', p):
                    self.fail('unreachable')

    def test_kit_v2_hash_matches_independent_known_serialization(self):
        import hashlib
        m = load()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'f').write_bytes(b'abc')
            root.chmod(0o700)
            (root / 'f').chmod(0o640)
            h = hashlib.sha256(b'nixkit-directory-hash-v2\0')
            def field(raw): return len(raw).to_bytes(8, 'big') + raw
            h.update(b'\x01' + field(b'') + (0o700).to_bytes(4, 'big'))
            h.update(b'\x02' + field(b'f') + (0o640).to_bytes(4, 'big') + field((3).to_bytes(8, 'big') + hashlib.sha256(b'abc').digest()))
            self.assertEqual(h.hexdigest(), m.kit_tree_digest(root))

    def test_production_cli_has_no_auth_or_path_override(self):
        for args in ([], ['--apply'], ['--receipt', '/fabricated'], ['--home', '/other']):
            result = subprocess.run(['python3', str(ROOT / 'scripts/knowledge_execution.py'), *args], text=True, capture_output=True)
            self.assertEqual(2, result.returncode)
            self.assertEqual('', result.stdout)

    def test_fsync_failure_before_removal_is_pure(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, p = self.fixture(m, base)
            before = m.snapshot(home / 'skills/watch')[0]
            with mock.patch.object(m.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
                with self.assertRaises(OSError):
                    with m.CacheTransaction(home, base / 'state', p) as tx:
                        tx.remove()
            self.assertEqual(before, m.snapshot(home / 'skills/watch')[0])

if __name__ == '__main__':
    unittest.main()
