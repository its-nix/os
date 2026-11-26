import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pack_build', ROOT / 'deploy/packs/build.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)
DOMAIN_PACKS = ('product', 'growth', 'sales', 'content', 'design', 'research', 'operations', 'data-ai')


class PackTests(unittest.TestCase):
    def test_pack_union_matches_locked_catalog(self):
        manifest = json.loads((pack.HERE / 'packs.json').read_text())
        names = pack.selection(manifest, ['engineering', 'core', *DOMAIN_PACKS, 'core'])
        self.assertEqual(names, sorted(manifest['kit_lock']['skills']))
        self.assertEqual(len(manifest['packs']['core']), 7)

    def test_each_domain_pack_adds_a_small_locked_selection_to_core(self):
        manifest = json.loads((pack.HERE / 'packs.json').read_text())
        core = set(manifest['packs']['core'])
        for role in DOMAIN_PACKS:
            with self.subTest(role=role):
                skills = manifest['packs'][role]
                selected = pack.selection(manifest, ['core', role, role])
                self.assertLessEqual(2, len(skills))
                self.assertLessEqual(len(skills), 5)
                self.assertEqual(len(skills), len(set(selected) - core))
                self.assertEqual(selected, sorted(core | set(skills)))

    def test_cli_exposes_domain_packs_for_builds_and_worktrees(self):
        for filename, option in [('build.py', '--role'), ('worktree.py', '--role'),
                                 ('worktree.py', '--task-pack')]:
            with self.subTest(filename=filename, option=option):
                help_text = subprocess.check_output(
                    [sys.executable, pack.HERE / filename, '--help'], text=True)
                choices = ','.join(sorted(('engineering', *DOMAIN_PACKS)))
                self.assertIn(option + ' {' + choices + '}', help_text)

    def test_existing_target_is_preserved_before_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                pack.build(Path(tmp) / 'missing-kit', Path(tmp), ['core'])

    def test_copy_verification_rejects_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'SKILL.md').symlink_to('/missing')
            with self.assertRaises(ValueError):
                pack.tree(Path(tmp))

    def test_mount_inventory_preserves_matches_and_refuses_conflicts(self):
        from unittest.mock import patch
        scanner_spec = importlib.util.spec_from_file_location('pack_mounts', pack.HERE / 'mounts.py')
        scanner = importlib.util.module_from_spec(scanner_spec)
        with patch.dict(sys.modules, build=pack):
            scanner_spec.loader.exec_module(scanner)
        with tempfile.TemporaryDirectory() as tmp:
            home, core = Path(tmp) / 'home', Path(tmp) / 'core'
            manifest = core / '.nixkit/packs.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'packs': {'core': ['handoff', 'grilling']}}))
            skill = core / 'grilling/SKILL.md'
            skill.parent.mkdir()
            skill.write_text('---\nname: grilling\n---\n')
            native = home / 'skills/grilling/SKILL.md'
            native.parent.mkdir(parents=True)
            native.write_bytes(skill.read_bytes())
            self.assertEqual(scanner.missing(home, core, 'hermes'), ['handoff'])
            native.write_text(skill.read_text() + 'changed')
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                scanner.missing(home, core, 'hermes')
            native.write_bytes(skill.read_bytes())
            second = home / 'skills/category/other/SKILL.md'
            second.parent.mkdir(parents=True)
            second.write_bytes(skill.read_bytes())
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                scanner.missing(home, core, 'hermes')
