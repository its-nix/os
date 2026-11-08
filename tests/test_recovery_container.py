"""Opt-in Docker canary; all state in this test is synthetic."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('HERMES_RECOVERY_TEST_IMAGE'), 'Docker canary requires explicit fixture image')
class ContainerRecoveryTests(unittest.TestCase):
    def test_real_image_restores_without_network_or_capabilities(self):
        image = 'nix-hermes-recovery-test:' + uuid.uuid4().hex
        self.addCleanup(subprocess.run, ['docker', 'image', 'rm', '-f', image], capture_output=True)
        with tempfile.TemporaryDirectory() as td:
            context = Path(td)
            archive = context / 'snapshot.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('config.yaml', 'example: true')
                zf.writestr('memories/MEMORY.md', 'synthetic learned context')
                zf.writestr('skills/learned/SKILL.md', '# Synthetic skill')
                zf.writestr('profiles/project/config.yaml', 'example: true')
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            (context / 'manifest.json').write_text(json.dumps({'archive_sha256': sha}))
            for name in ('Dockerfile', 'recovery.py'):
                shutil.copyfile(ROOT / 'automation/recovery' / name, context / name)
            build = subprocess.run([
                'docker', 'build', '--network', 'none', '--build-arg',
                'HERMES_IMAGE=' + os.environ['HERMES_RECOVERY_TEST_IMAGE'],
                '--tag', image, str(context),
            ], capture_output=True, text=True, timeout=300)
            self.assertEqual(0, build.returncode, build.stderr)
            verify = subprocess.run([
                'docker', 'run', '--rm', '--network', 'none', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', image, 'verify',
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(0, verify.returncode, verify.stderr)
            self.assertIn('4 files checked; no services started', verify.stdout)
