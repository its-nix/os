"""Opt-in Docker lifecycle check. The initial catalog installation requires network access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment', ROOT / 'scripts/deploy-os.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
PYTHON = '/opt/hermes/.venv/bin/python'
LOAD = """
from hermes_cli.config import load_config
from plugins.memory import load_memory_provider
from hindsight_client import Hindsight
import sys
name = load_config()['memory']['provider']
assert name == 'hindsight' and sys.dont_write_bytecode
provider = load_memory_provider(name)
assert provider is not None and provider.is_available()
"""


@unittest.skipUnless(os.getenv('HINDSIGHT_RUNTIME_TEST') == '1', 'requires Docker and catalog access')
class HindsightRuntimeTests(unittest.TestCase):
    def test_enabled_provider_survives_second_deployment_and_container_replacement(self):
        image = json.loads((ROOT / 'deploy/versions.json').read_text())['hermes']['image']
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            home = state / 'hermes'
            home.mkdir()
            (state / 'hindsight.env').write_text(deployment.HINDSIGHT_KEY_NAME + '=' + '0' * 64 + '\n')
            mount = f'type=bind,source={home},target=/opt/data'
            for first in (True, False):
                container = deployment.run(['docker', 'run', '-d', '--network', 'bridge' if first else 'none',
                                            '--mount', mount,
                                            '--entrypoint', 'sleep', image, 'infinity'])
                try:
                    def run(argv):
                        if argv[:2] == ['docker', 'exec']:
                            self.assertEqual('nixos-hermes', argv[4])
                            argv = [*argv[:4], container, *argv[5:]]
                        return deployment.run(argv)

                    if first:
                        deployment.run(['docker', 'exec', container, 'chown', '-R', '10000:10000', '/opt/data'])
                    deployment.preflight_memory(ROOT, state, image, run)
                    pending = deployment.configure_memory(state, run)
                    self.assertEqual(first, pending.exists())
                    pending.unlink(missing_ok=True)
                    run(['docker', 'exec', '-u', '10000', 'nixos-hermes', PYTHON, '-c', LOAD])
                    deployment.check_installed_plugin(home)
                finally:
                    subprocess.run(['docker', 'rm', '-fv', container], check=True, stdout=subprocess.DEVNULL)
