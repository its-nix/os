"""Safe local fixture tests; never touch real Hermes profiles."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reconcile", ROOT / "scripts/reconcile-live-lead-contracts.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = {}
        for name in mod.PROFILES:
            p = self.root / name / "SOUL.md"; p.parent.mkdir()
            replacements = "\n".join(x[0] for x in mod.REPLACEMENTS[name])
            p.write_text(f"# {name}\n\nUnique unrelated content.\n\n{replacements}\n\n## Mission\nkeep mission\n\n## Handoff to LNX\nlegacy handoff wording\n", encoding="utf-8")
            p.chmod(0o640); self.paths[name] = p
        self.backups = self.root / "external-backups"
        self.backups.mkdir(mode=0o700)
    def tearDown(self): self.tmp.cleanup()
    def test_preview_is_pure_and_diff_exact(self):
        originals = {n:p.read_bytes() for n,p in self.paths.items()}
        diffs = mod.reconcile(self.paths, backup_dir=self.backups)
        self.assertEqual(3, len(diffs)); self.assertTrue(all("proposed" in d for d in diffs))
        self.assertEqual(originals, {n:p.read_bytes() for n,p in self.paths.items()})
        self.assertEqual([], list(self.backups.iterdir()))
    def test_apply_preserves_unrelated_and_mode_and_idempotent(self):
        originals = {n:p.read_text() for n,p in self.paths.items()}
        mod.reconcile(self.paths, True, self.backups)
        for n,p in self.paths.items():
            self.assertIn("Unique unrelated content.", p.read_text())
            self.assertIn("legacy handoff wording", p.read_text())
            self.assertIn("notify-subscribe", p.read_text())
            self.assertEqual(0o640, p.stat().st_mode & 0o777)
            self.assertTrue(any(x.read_text() == originals[n] for x in self.backups.glob(f"{n}-*.md")))
        after = {n:p.read_bytes() for n,p in self.paths.items()}
        mod.reconcile(self.paths, True, self.backups)
        self.assertEqual(after, {n:p.read_bytes() for n,p in self.paths.items()})
    def test_missing_duplicate_or_changed_anchor_refused(self):
        p = self.paths['pulse']; p.write_text(p.read_text().replace("Escalate genuine product/commercial judgment", "Altered anchor"))
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
        p.unlink(); p.write_text("# changed\n")
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
    def test_symlink_and_hardlink_refused(self):
        p = self.paths['pulse']; original = p.read_bytes(); target = self.root/'target'; target.write_bytes(original)
        p.unlink(); p.symlink_to(target)
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
        p.unlink(); p.hardlink_to(target)
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
    def test_world_writable_and_unsafe_backup_refused(self):
        p = self.paths['pulse']; p.chmod(0o666)
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
        p.chmod(0o640); self.backups.chmod(0o755)
        with self.assertRaises(ValueError): mod.reconcile(self.paths, True, self.backups)
    def test_unexpected_managed_section_refused(self):
        mod.reconcile(self.paths, True, self.backups)
        p = self.paths['pulse']; p.write_text(p.read_text().replace("CLI support", "different wording"))
        with self.assertRaises(ValueError): mod.reconcile(self.paths)

if __name__ == '__main__': unittest.main()
