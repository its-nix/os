import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pack_build', ROOT / 'deploy/packs/build.py')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PackTests(unittest.TestCase):
    def test_pack_union_matches_locked_catalog(self):
        manifest = json.loads((pack.HERE / 'packs.json').read_text())
        names = pack.selection(manifest, ['engineering', 'core', 'core'])
        self.assertEqual(names, sorted(manifest['kit_lock']['skills']))
        self.assertEqual(len(manifest['packs']['core']), 7)

    def test_existing_target_is_preserved_before_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                pack.build(Path(tmp) / 'missing-kit', Path(tmp), ['core'])

    def test_copy_verification_rejects_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'SKILL.md').symlink_to('/missing')
            with self.assertRaises(ValueError):
                pack.tree(Path(tmp))
