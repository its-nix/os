"""Pending activation is diagnostic data, never production authority."""
import copy
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

from unittest import mock
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def load():
    spec = importlib.util.spec_from_file_location('activation', ROOT / 'scripts/preview-knowledge-activation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class ActivationTests(unittest.TestCase):
    def test_exact_pending_default_only_contract(self):
        m = load()
        p = m.load_proposal()
        self.assertEqual('pending_approval', p['status'])
        self.assertEqual(('lnx', 'default'), (p['profile'], p['hermes_profile']))
        self.assertNotIn('approved_by', json.dumps(p))
        self.assertEqual(5, len(p['preservation']))
        self.assertEqual(5, len(p['cache']['files']))
        self.assertEqual(17, len(p['effective_skills']))
        self.assertEqual(12, len(p['install_targets']))
        self.assertIn('nix-knowledge', p['install_targets'])
        self.assertNotIn('nix-document-design', p['effective_skills'])
        self.assertEqual(set(p['effective_skills']) - {r['skill'] for r in p['preservation']}, set(p['install_targets']))

    def test_durable_activation_packets_contain_only_semantic_inventory(self):
        forbidden = {'identity', 'device', 'inode', 'uid', 'gid', 'mtime_ns', 'size'}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden & value.keys(), 'runtime identity in durable proposal')
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
        for path in (ROOT / 'skills/activation-proposals').glob('*.json'):
            with self.subTest(packet=path.name):
                check(json.loads(path.read_text()))

    def test_proposal_tampering_fails_closed(self):
        m = load()
        p = m.load_proposal()
        for key, value in [('profile', 'pulse'), ('status', 'approved'), ('install_targets', ['nix-knowledge'])]:
            bad = copy.deepcopy(p)
            bad[key] = value
            with self.assertRaises(m.installer.Refusal):
                m.validate_proposal(bad)

    def test_production_pending_gate_rejects_false_receipts(self):
        spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install-skills.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for receipts in ([], ['skills/approvals/lnx-watch-a9cb5b57.json'], ['fake-approved-by-Lior.json']):
            with self.assertRaisesRegex(m.Refusal, 'pending.*authenticated'):
                m._require_knowledge_activation_authority('lnx', {'nix-knowledge'}, receipts)
        m._require_knowledge_activation_authority('pulse', {'technical-project-management'}, [])

    def test_apply_is_not_a_diagnostic_option(self):
        r = subprocess.run(['python3', str(ROOT / 'scripts/preview-knowledge-activation.py'), '--apply'], capture_output=True, text=True)
        self.assertNotEqual(0, r.returncode)
        self.assertNotIn('APPLY profile', r.stdout)

    def test_live_inventory_is_explicit_opt_in(self):
        m = load()
        p = m.load_proposal()
        out = m.summary(p)
        self.assertEqual('NOT authorized production preview', out['authority'])
        self.assertFalse(out['live_validated'])
        self.assertEqual(p['install_targets'], out['install_targets'])

    def fixture(self, m, home):
        root = home / 'skills/watch'
        (root / 'scripts/__pycache__').mkdir(parents=True)
        (root / 'SKILL.md').write_text('---\nname: watch\n---\n')
        for name in ('download', 'frames', 'local_whisper', 'runtime', 'transcript'):
            (root / f'scripts/__pycache__/{name}.cpython-311.pyc').write_bytes(name.encode())
        rows = m.inventory(root)
        files = [r for r in rows if r['path'].endswith('.pyc')]
        skip = {r['path'] for r in files} | {'scripts/__pycache__'}
        return {'cache': {'files': files, 'inventory': rows,
                         'before_tree_sha256': m.installer._skill_tree_digest(root),
                         'remove_directory_if_empty': 'scripts/__pycache__'},
                'install_targets': ['watch'],
                'acquired_target_closures': {'watch': [r.copy() for r in rows if r['path'] not in skip]}}

    def test_projection_is_pure_and_exact_not_a_hash_ignore(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = self.fixture(m, home)
            before = m.inventory(home / 'skills/watch')
            m.validate_live_targets(p, home)
            self.assertEqual(before, m.inventory(home / 'skills/watch'))
            extra = home / 'skills/watch/scripts/__pycache__/unreviewed.pyc'
            extra.write_bytes(b'unreviewed')
            with self.assertRaisesRegex(m.installer.Refusal, 'drift'):
                m.validate_live_targets(p, home)
            self.assertTrue(extra.exists())

    def test_preinvocation_same_content_mode_replacement_is_not_drift(self):
        m = load()
        for relative in ('SKILL.md', 'scripts/__pycache__/runtime.cpython-311.pyc'):
            with tempfile.TemporaryDirectory() as td:
                home = Path(td)
                p = self.fixture(m, home)
                target = home / 'skills/watch' / relative
                original = target.read_bytes()
                replacement = target.with_name('replacement')
                replacement.write_bytes(original)
                replacement.replace(target)
                m.validate_live_targets(p, home)
                self.assertEqual(original, target.read_bytes())

    def test_changed_bytes_or_modes_refused_without_cleanup(self):
        m = load()
        for relative in ('SKILL.md', 'scripts/__pycache__/runtime.cpython-311.pyc'):
            for change in ('bytes', 'mode'):
                with self.subTest(relative=relative, change=change), tempfile.TemporaryDirectory() as td:
                    home = Path(td)
                    p = self.fixture(m, home)
                    target = home / 'skills/watch' / relative
                    if change == 'bytes':
                        target.write_bytes(b'changed')
                    else:
                        target.chmod(target.stat().st_mode ^ 0o100)
                    with self.assertRaisesRegex(m.installer.Refusal, 'drift'):
                        m.validate_live_targets(p, home)
                    self.assertTrue(target.is_file())

    def test_runtime_identity_injection_rejected_even_with_matching_digest(self):
        m = load()
        for key in ('identity', 'device', 'inode', 'uid', 'gid', 'mtime_ns', 'size'):
            p = m.load_proposal()
            p['cache']['inventory'][0][key] = 123
            with mock.patch.object(m, 'PROPOSAL_SHA256', m.canonical_digest(p)):
                with self.assertRaisesRegex(m.installer.Refusal, 'runtime identity forbidden'):
                    m.validate_proposal(p)

    def test_inflight_same_content_replacement_refused(self):
        m = load()
        for phase in ('open', 'read', 'after_tree'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                target = root / 'payload'
                target.write_bytes(b'original')
                replacement = root.parent / (root.name + '-replacement')
                def replace():
                    replacement.write_bytes(target.read_bytes())
                    replacement.chmod(target.stat().st_mode)
                    replacement.replace(target)
                real_open, real_read = os.open, os.read
                real_digest = m.installer._skill_tree_digest
                calls = []
                def opening(path, flags, *args, **kwargs):
                    if Path(path) == target and not calls:
                        calls.append(True)
                        replace()
                    return real_open(path, flags, *args, **kwargs)
                def reading(fd, count):
                    data = real_read(fd, count)
                    if not calls:
                        calls.append(True)
                        replace()
                    return data
                def digest(path):
                    result = real_digest(path)
                    calls.append(True)
                    if len(calls) == 2:
                        replace()
                    return result
                if phase == 'after_tree':
                    guard = mock.patch.object(m.installer, '_skill_tree_digest', side_effect=digest)
                elif phase == 'open':
                    guard = mock.patch.object(m.os, 'open', side_effect=opening)
                else:
                    guard = mock.patch.object(m.os, 'read', side_effect=reading)
                # Isolate inventory's own open/read guards from the separate
                # descriptor-safe digest implementation, which also uses os.
                digest_guard = (mock.patch.object(m.installer, '_skill_tree_digest', return_value='stable')
                                if phase != 'after_tree' else nullcontext())
                with digest_guard, guard, self.assertRaisesRegex(m.installer.Refusal, 'replaced|changed'):
                    m.inventory(root)
                self.assertEqual(b'original', target.read_bytes())

    def test_target_closure_mode_or_extra_file_cannot_be_silently_overwritten(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = self.fixture(m, home)
            p['acquired_target_closures']['watch'][0]['mode'] ^= 0o100
            with self.assertRaisesRegex(m.installer.Refusal, 'closure drift'):
                m.validate_live_targets(p, home)
            self.assertTrue((home / 'skills/watch/SKILL.md').is_file())

    def test_symlink_inventory_refused_without_following(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self.fixture(m, home)
            (home / 'skills/watch/link').symlink_to(home / 'skills/watch/SKILL.md')
            with self.assertRaises(m.installer.Refusal):
                m.inventory(home / 'skills/watch')

    def test_dirty_diagnostic_and_nondefault_home_refused(self):
        m = load()
        p = m.load_proposal()
        with self.assertRaisesRegex(m.installer.Refusal, 'default home'):
            m.live_preview(p, Path('/not-default'), Path('/unused-kit'))
        with mock.patch.object(m.installer, '_check_no_symlink'), mock.patch.object(m.installer, '_run', return_value=' M dirty'):
            with self.assertRaisesRegex(m.installer.Refusal, 'clean committed'):
                m.live_preview(p, Path.home() / '.hermes', Path('/unused-kit'))

    def test_github_subdirectory_catalog_binds_each_package_not_parent(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sources.json'
            names = m.installer.NIXKIT_SKILLS - {'watch', 'nix-knowledge', 'nix-document-design'}
            path.write_text(json.dumps([
                {'repo': 'example/source', 'ref': 'b' * 40, 'skills': sorted(names)},
                {'repo': 'its-nix/os', 'ref': '1182c51c33ccb8a6c3b095bf551864fc5cb061c6',
                 'path': 'skills', 'skills': ['nix-knowledge', 'nix-document-design']},
                {'local': 'skills', 'skills': ['watch']}
            ]))
            catalog = m.installer._source_catalog(path)
            for name in ('nix-knowledge', 'nix-document-design'):
                self.assertEqual('skills/' + name, catalog[name][2])
            self.assertEqual('skills/watch', catalog['watch'][2])
            p = json.loads((ROOT / 'skills/proposals/lnx-nix-knowledge-1182c51c.json').read_text())
            m.installer._validate_proposal(p, p['proposal_id'], 'lnx', {'nix-knowledge'}, catalog)
            bad = copy.deepcopy(p)
            bad['skill_path'] = 'skills/nix-document-design'
            with self.assertRaises(m.installer.Refusal):
                m.installer._validate_proposal(bad, p['proposal_id'], 'lnx', {'nix-knowledge'}, catalog)

    def test_real_wrapper_pending_preview_and_apply_reject_false_receipts(self):
        for flag in ([], ['--apply']):
            command = ['python3', str(ROOT / 'scripts/install-skills.py'), '--profile', 'lnx',
                       '--nixkit', '/does-not-exist', '--skill', 'nix-knowledge',
                       '--approval', 'fake-approved-by-Lior.json'] + flag
            r = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(2, r.returncode)
            self.assertIn('pending: authenticated', r.stderr)
            self.assertEqual('', r.stdout)

if __name__ == '__main__':
    unittest.main()
