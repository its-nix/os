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
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.tmp.name)
        self.paths = {}
        for name in mod.PROFILES:
            p = self.root / name / "SOUL.md"; p.parent.mkdir(); p.parent.chmod(0o700)
            replacements = "\n".join(x[0] for x in mod.REPLACEMENTS[name])
            p.write_text(f"# {name}\n\nUnique unrelated content.\n\n{replacements}\n\n## Mission\nkeep mission\n\n## Handoff to LNX\nlegacy handoff wording\n", encoding="utf-8")
            p.chmod(0o640); self.paths[name] = p
        self.backups = self.root / "external-backups"
        self.backups.mkdir(mode=0o700)
        self.backups.chmod(0o700)
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
    def test_unsafe_backup_ancestry_and_target_ancestry_refused(self):
        alias = self.root / "alias"; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): mod.reconcile(self.paths, True, alias / "external-backups")
        real = self.root / "outside"; real.mkdir(); real.chmod(0o700)
        link = self.root / "redirect"; link.symlink_to(real, target_is_directory=True)
        victim = link / "pulse" / "SOUL.md"
        with self.assertRaises((ValueError, OSError)): mod.reconcile({**self.paths, "pulse": victim})
    def test_target_owner_mode_symlink_and_anchor_order(self):
        for n, p in self.paths.items():
            original = p.read_text(); preview = mod.transform(n, original)
            self.assertLess(preview.index("keep mission") + len("keep mission"), preview.index(mod.SECTION.strip()))
            self.assertLess(preview.index(mod.SECTION.strip()), preview.index("## Handoff to LNX"))
        p = self.paths['pulse']; p.chmod(0o666)
        with self.assertRaises(ValueError): mod.reconcile(self.paths)
        p.chmod(0o640)
        if os.getuid() == 0:
            os.chown(p, 1, -1)
            with self.assertRaises(ValueError): mod.reconcile(self.paths)
            os.chown(p, os.getuid(), -1)
    def test_anchor_drift_and_repeated_transform(self):
        p = self.paths['pulse']; original = p.read_text()
        self.assertEqual(mod.transform('pulse', original), mod.transform('pulse', original))
        with self.assertRaises(ValueError): mod.transform('pulse', original.replace("## Mission", "## Changed", 1))
    def test_partial_commit_rolls_back(self):
        import unittest.mock as mock
        originals = {n:p.read_bytes() for n,p in self.paths.items()}
        real_replace = os.replace; count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2: raise OSError("injected replacement failure")
            return real_replace(*args, **kwargs)
        with mock.patch.object(mod.os, "replace", side_effect=fail_second):
            with self.assertRaises(OSError): mod.reconcile(self.paths, True, self.backups)
        self.assertEqual(originals, {n:p.read_bytes() for n,p in self.paths.items()})
        self.assertEqual([], list(self.backups.iterdir()))
    def test_unsafe_backup_ancestry_and_symlink_backup_refused(self):
        alias = self.root / "backup-link"; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): mod.reconcile(self.paths, True, alias)
        unsafe = self.root / "unsafe"; unsafe.mkdir(); unsafe.chmod(0o777)
        with self.assertRaises(ValueError): mod.reconcile(self.paths, True, unsafe)
    def test_unexpected_managed_section_refused(self):
        mod.reconcile(self.paths, True, self.backups)
        p = self.paths['pulse']; p.write_text(p.read_text().replace("CLI support", "different wording"))
        with self.assertRaises(ValueError): mod.reconcile(self.paths)

if __name__ == '__main__': unittest.main()
