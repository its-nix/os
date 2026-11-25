"""Hindsight memory (PRD 7.4): pinned local server, one bank per profile, never a shared or cloud bank."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment_memory', ROOT / 'scripts/deploy-os.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
deployment.HINDSIGHT_UID, deployment.HINDSIGHT_GID = os.getuid(), os.getgid()
COMPOSE = (ROOT / 'deploy/compose.yaml').read_text()
probe_spec = importlib.util.spec_from_file_location('redaction_probe', ROOT / 'deploy/hindsight/redaction_probe.py')
probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(probe)
CATALOG = (f'name: hindsight\nrepo: https://github.com/vectorize-io/hindsight\nsha: {deployment.HINDSIGHT_PLUGIN_SHA}\n'
           'subdir: hindsight-integrations/hermes\nversion: "1.2.4"\n')


def service(name):
    return COMPOSE.split(f'\n  {name}:\n', 1)[1].split('\n  # ', 1)[0].split('\n  reviewer:\n', 1)[0]


class FakeHermes:
    """Records native CLI calls. `plugins install --ref X --no-enable` installs commit `plugin_sha`
    (what the network delivered) DISABLED; only `config set memory.provider` activates it."""

    def __init__(self, state, providers=None, plugin_sha=deployment.HINDSIGHT_PLUGIN_SHA, catalog=CATALOG,
                 fail_set=False):
        self.state, self.providers, self.sha, self.calls = state, dict(providers or {}), plugin_sha, []
        self.config_at_install, self.catalog, self.fail_set = {}, catalog, fail_set

    def home(self, profile):
        return self.state / 'hermes' if profile == 'default' else self.state / 'hermes/profiles' / profile

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[5:] == ['cat', '/opt/hermes/plugin-catalog/hindsight.yaml']:
            return self.catalog
        profile, command = argv[7], argv[8:]
        if command == ['config', 'get', '--json', 'memory.provider']:
            if profile not in self.providers:
                raise subprocess.CalledProcessError(1, argv, output='Config key not set: memory.provider\n')
            return json.dumps(self.providers[profile])
        if command[:2] == ['plugins', 'install']:
            config = self.home(profile) / 'hindsight/config.json'
            self.config_at_install[profile] = json.loads(config.read_text()) if config.exists() else None
            plugin = self.home(profile) / 'plugins/hindsight'
            plugin.mkdir(parents=True)
            (plugin / '.hermes-catalog.json').write_text(json.dumps({'sha': self.sha}))
            if '--enable' in command:
                self.providers[profile] = 'hindsight'
            return ''
        if command == ['config', 'set', 'memory.provider', 'hindsight']:
            if self.fail_set:
                raise subprocess.CalledProcessError(1, argv)
            self.providers[profile] = 'hindsight'
            return ''
        raise AssertionError(argv)


class HindsightMemoryTests(unittest.TestCase):
    def state(self, td, profiles=('engineer', 'reviewer', 'demo')):
        state = Path(td) / 'state'
        (state / 'hermes/profiles').mkdir(parents=True)
        for name in profiles:
            (state / 'hermes/profiles' / name).mkdir()
        if 'demo' in profiles:
            (state / 'hermes/profiles/demo/nix-venture.json').write_text('{"venture": "demo"}')
        deployment.ensure_hindsight_state(state)
        return state

    def key(self, state):
        return deployment.hindsight_api_key(state)

    def test_each_profile_gets_its_own_bank_and_unknown_profiles_none(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td, ('engineer', 'reviewer', 'demo', 'personal', 'scratch-clone'))
            (state / 'hermes/profiles/outside').symlink_to(td)
            banks = {profile: bank for profile, _, bank in deployment.memory_banks(state)}
        self.assertEqual({'default': 'lnx', 'engineer': 'engineer', 'reviewer': 'reviewer',
                          'demo': 'venture-demo', 'personal': 'personal'}, banks)

    def test_fresh_profiles_are_configured_before_the_provider_activates(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td)
            hermes = FakeHermes(state, providers={'engineer': ''})
            pending = deployment.configure_memory(state, hermes)
            self.assertTrue(pending.exists())  # the gateway must restart to import the plugin client
            for profile, bank in (('default', 'lnx'), ('engineer', 'engineer'), ('reviewer', 'reviewer'),
                                  ('demo', 'venture-demo')):
                written = json.loads((hermes.home(profile) / 'hindsight/config.json').read_text())
                self.assertEqual(written, hermes.config_at_install[profile])  # config precedes enabling
                self.assertEqual(('local_external', 'http://127.0.0.1:18888', bank, self.key(state)),
                                 (written['mode'], written['api_url'], written['bank_id'], written['api_key']))
                self.assertFalse(any(written[key] for key in deployment.HINDSIGHT_CROSS_BANK_KEYS
                                     if key in written))
                self.assertFalse(written['recall_indicator'] or written['retain_indicator'])
                self.assertEqual('hindsight', hermes.providers[profile])
                self.assertEqual(0o600, (hermes.home(profile) / 'hindsight/config.json').stat().st_mode & 0o777)
            installs = [c for c in hermes.calls if c[8:10] == ['plugins', 'install']]
            self.assertEqual(4, len(installs))
            self.assertTrue(all(c[8:] == ['plugins', 'install', 'hindsight', '--ref', deployment.HINDSIGHT_PLUGIN_SHA,
                                          '--yes-deps', '--no-enable'] for c in installs))
            # The catalog pin is read once, before the first install.
            reads = [i for i, c in enumerate(hermes.calls) if c[5:6] == ['cat']]
            self.assertEqual(1, len(reads))
            self.assertLess(reads[0], hermes.calls.index(installs[0]))
            self.assertTrue(all(c[:5] == ['docker', 'exec', '-u', '10000', 'nixos-hermes'] for c in hermes.calls))
            self.assertFalse((state / 'hermes/.hindsight').exists())  # never the shared fallback
            self.assertFalse(list(state.glob('hermes/**/.deploy-enable-pending')))

            pending.unlink()
            again = FakeHermes(state, providers=hermes.providers)
            deployment.configure_memory(state, again)
            self.assertFalse(pending.exists())
            self.assertEqual([], [c for c in again.calls if 'install' in c or 'set' in c or 'cat' in c])

    def test_plugin_pin_is_verified_before_install_and_before_activation(self):
        for name, catalog in (('other commit', CATALOG.replace(deployment.HINDSIGHT_PLUGIN_SHA, '0' * 40)),
                              ('other repository', CATALOG.replace('vectorize-io/hindsight', 'someone/hindsight')),
                              ('other directory', CATALOG.replace('integrations/hermes', 'integrations/x'))):
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                state = self.state(td)
                hermes = FakeHermes(state, catalog=catalog)
                with self.assertRaisesRegex(ValueError, 'catalog'):
                    deployment.configure_memory(state, hermes)
                self.assertEqual([], [c for c in hermes.calls if 'install' in c or 'set' in c])
                self.assertFalse(list(state.glob('hermes/**/hindsight/config.json')))  # nothing written
        with tempfile.TemporaryDirectory() as td:  # the download is not the reviewed commit
            state = self.state(td)
            hermes = FakeHermes(state, plugin_sha='0' * 40)
            with self.assertRaisesRegex(ValueError, 'reviewed commit'):
                deployment.configure_memory(state, hermes)
            installs = [c for c in hermes.calls if 'install' in c]
            self.assertEqual(1, len(installs))
            self.assertIn('--no-enable', installs[0])
            self.assertNotIn('--enable', installs[0])
            self.assertEqual([], [c for c in hermes.calls if 'set' in c])
            self.assertNotEqual('hindsight', hermes.providers.get('default'))  # provider stays inactive
            again = FakeHermes(state, providers=hermes.providers, plugin_sha='0' * 40)
            with self.assertRaisesRegex(ValueError, 'reviewed commit'):  # never mistaken for `memory off`
                deployment.configure_memory(state, again)
            self.assertEqual([], [c for c in again.calls if 'set' in c])

    def test_interrupted_activation_is_finished_not_mistaken_for_memory_off(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td, ('engineer',))
            hermes = FakeHermes(state, fail_set=True)
            with self.assertRaises(subprocess.CalledProcessError):
                deployment.configure_memory(state, hermes)
            self.assertTrue((state / 'hermes/hindsight/.deploy-enable-pending').exists())
            again = FakeHermes(state, providers=hermes.providers)
            deployment.configure_memory(state, again)
            self.assertEqual('hindsight', again.providers['default'])
            self.assertEqual('hindsight', again.providers['engineer'])
            self.assertFalse((state / 'hermes/hindsight/.deploy-enable-pending').exists())

    def test_existing_memory_is_validated_never_rewritten(self):
        cases = {
            'other bank': {'bank_id': 'lnx'},
            'cloud': {'mode': 'cloud', 'api_url': 'https://api.hindsight.vectorize.io'},
            'shared read': {'recall_additional_banks': ['personal']},
            'shared write': {'additional_banks': ['lnx']},
            'repository banks': {'trusted_project_dirs': ['/opt/data']},
            'no api key': {'api_key': ''},
            'other api key': {'api_key': '0' * 64},
        }
        for name, change in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                state = self.state(td)
                config = state / 'hermes/profiles/engineer/hindsight/config.json'
                config.parent.mkdir()
                body = json.dumps(dict(deployment.hindsight_profile_config('engineer', 'engineer', self.key(state)),
                                       **change))
                config.write_text(body)
                with self.assertRaises(ValueError):
                    deployment.configure_memory(state, FakeHermes(state))
                self.assertEqual(body, config.read_text())

    def test_profile_overrides_shared_fallback_and_other_providers(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td)
            (state / 'hermes/profiles/demo/.env').write_text('OPENAI_KEY=x\nHINDSIGHT_BANK_ID=lnx\n')
            with self.assertRaisesRegex(ValueError, 'HINDSIGHT_'):
                deployment.configure_memory(state, FakeHermes(state))
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td)
            (state / 'hermes/.hindsight').mkdir()
            (state / 'hermes/.hindsight/config.json').write_text('{"mode": "cloud"}')
            with self.assertRaisesRegex(ValueError, 'shared'):
                deployment.configure_memory(state, FakeHermes(state))
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td)
            hermes = FakeHermes(state, providers={'reviewer': 'honcho'})
            deployment.configure_memory(state, hermes)
            self.assertFalse((state / 'hermes/profiles/reviewer/hindsight').exists())
            self.assertEqual('honcho', hermes.providers['reviewer'])
        with tempfile.TemporaryDirectory() as td:  # `hermes memory off` after setup is respected
            state = self.state(td)
            hermes = FakeHermes(state)
            deployment.configure_memory(state, hermes)
            hermes.providers['engineer'] = ''
            again = FakeHermes(state, providers=hermes.providers)
            deployment.configure_memory(state, again)
            self.assertEqual('', again.providers['engineer'])
            self.assertFalse(any(c[7] == 'engineer' and 'set' in c for c in again.calls))
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td)
            with self.assertRaisesRegex(ValueError, 'reviewed commit'):
                deployment.configure_memory(state, FakeHermes(state, plugin_sha='0' * 40))

    def test_state_directories_are_new_private_and_never_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            deployment.ensure_hindsight_state(state)
            for name in ('hindsight', 'hindsight/codex'):
                self.assertEqual(0o700, (state / name).stat().st_mode & 0o777)
            key = deployment.hindsight_api_key(state)
            self.assertRegex(key, r'^[0-9a-f]{64}$')
            self.assertEqual(0o600, (state / 'hindsight.env').stat().st_mode & 0o777)
            (state / 'hindsight/codex/auth.json').write_text('{}')
            deployment.ensure_hindsight_state(state)
            self.assertTrue((state / 'hindsight/codex/auth.json').exists())
            self.assertEqual(key, deployment.hindsight_api_key(state))  # generated once, never rotated by a deploy
            (state / 'hindsight.env').write_text('HINDSIGHT_API_TENANT_API_KEY=short\n')
            with self.assertRaisesRegex(ValueError, 'malformed'):
                deployment.ensure_hindsight_state(state)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'hindsight').mkdir()
            (Path(td) / 'hindsight/codex').symlink_to(td)
            with self.assertRaises(ValueError):
                deployment.ensure_hindsight_state(Path(td))

    def test_server_is_pinned_local_loopback_only_and_resource_capped(self):
        hindsight = service('hindsight')
        self.assertIn('image: ' + deployment.HINDSIGHT_IMAGE + '  #', hindsight)
        self.assertRegex(deployment.HINDSIGHT_IMAGE, r'@sha256:[0-9a-f]{64}$')
        self.assertEqual(['      - 127.0.0.1:18888:8888'], [l for l in hindsight.splitlines() if l.startswith('      - ') and ':8888' in l])
        self.assertTrue(deployment.HINDSIGHT_URL.startswith('http://127.0.0.1:18888'))
        for required in ('networks: [memory]', 'mem_limit: 1536m', 'cpus: 1.0', 'cap_drop: [ALL]',
                         'HINDSIGHT_API_LLM_PROVIDER: openai-codex',
                         'HINDSIGHT_API_LLM_CODEX_HOME: /var/lib/hindsight/codex',
                         'HINDSIGHT_API_EMBEDDINGS_PROVIDER: local', 'HINDSIGHT_API_RERANKER_PROVIDER: rrf',
                         'HINDSIGHT_API_DECISION_MODEL_PROVIDER: none', "HF_HUB_OFFLINE: '1'",
                         "HINDSIGHT_API_MCP_ENABLED: 'false'", 'HINDSIGHT_API_RETAIN_MISSION:',
                         '}/hindsight\n        target: /var/lib/hindsight\n',
                         'HOME=/var/lib/hindsight exec /app/start-all.sh',
                         'HINDSIGHT_API_TENANT_EXTENSION: hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension',
                         '}/hindsight.env\n        required: true',
                         "HINDSIGHT_API_ENABLE_BANK_LLM_HEALTH: 'true'"):
            self.assertIn(required, hindsight)
        for forbidden in ('network_mode', 'vectorize.io', 'docker.sock', '/hermes', '/t3/', '/reviewer/'):
            self.assertNotIn(forbidden, hindsight)
        self.assertNotIn('vectorize.io', COMPOSE)

    def test_apply_requires_healthy_pinned_server_and_loopback_listener(self):
        source = (ROOT / 'scripts/deploy-os.py').read_text()
        self.assertIn("'3773', '18888'", source)
        self.assertIn("'nixos-hindsight']) \\\n                != HINDSIGHT_IMAGE + ' healthy'", source)
        self.assertNotIn('hindsight', json.dumps(json.loads((ROOT / 'deploy/versions.json').read_text())))

    def test_recovery_stops_the_server_after_hermes_and_starts_it_first(self):
        spec = importlib.util.spec_from_file_location('full_create_memory', ROOT / 'automation/recovery/full_create.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertLess(module.CONTAINERS.index('nixos-hermes'), module.CONTAINERS.index('nixos-hindsight'))
        unit = (ROOT / 'automation/recovery/nixos-recovery.service').read_text()
        self.assertIn('for c in nixos-hindsight nixos-hermes', unit)

    def test_founder_login_is_hindsights_own_codex_device_login(self):
        script = (ROOT / 'deploy/hindsight/codex-login.sh').read_text()
        self.assertIn("docker inspect --format '{{.Config.Image}}' nixos-t3", script)  # pinned codex, no new fetch
        self.assertIn('login --device-auth', script)
        self.assertIn('cli_auth_credentials_store="file"', script)
        self.assertIn('source=/srv/nix-clean/hindsight/codex,target=/codex', script)
        self.assertIn('--user 1000:1000', script)
        self.assertIn('docker restart --timeout 60 nixos-hindsight', script)
        for forbidden in ('/srv/nix-clean/hermes', 't3/home', 'reviewer/home', 'auth.json /', 'cp ', 'curl',
                          'hindsight.env'):
            self.assertNotIn(forbidden, script)
        # The model check runs inside the server with its own key: the key never enters a host argv.
        self.assertIn('docker exec nixos-hindsight', script)
        self.assertIn('os.environ["HINDSIGHT_API_TENANT_API_KEY"]', script)
        self.assertEqual(0, subprocess.run(['sh', '-n', str(ROOT / 'deploy/hindsight/codex-login.sh')]).returncode)
        with tempfile.TemporaryDirectory() as td:
            deployment.install_hindsight_login(ROOT, Path(td))
            installed = Path(td) / 'nix-hindsight-login'
            self.assertEqual(script, installed.read_text())
            self.assertEqual(0o755, installed.stat().st_mode & 0o777)
        self.assertIn('ssh -t -l nixos vps nix-hindsight-login', (ROOT / 'deploy/hindsight/README.md').read_text())


class RedactionProbeTests(unittest.TestCase):
    """deploy/hindsight/redaction_probe.py runs inside the Hermes image before every rollout."""

    class Manager:
        def sync_all(self, user, assistant):
            return _redact_for_provider(user, assistant)
        prefetch_all = queue_prefetch_all = handle_tool_call = on_memory_write = sync_all
        on_turn_start = on_delegation = on_pre_compress = sync_all

        def on_session_end(self, messages):
            return _redact_messages_for_egress(messages)

    def test_a_scrub_that_masks_every_category_passes(self):
        def mask(text):
            for _, secret in probe.samples().values():
                text = text.replace(secret, '[redacted]')
            return text
        self.assertEqual([], probe.check(mask, self.Manager))

    def test_leaks_and_unscrubbed_hand_offs_fail(self):
        failures = probe.check(lambda text: text, self.Manager)
        self.assertEqual(len(probe.samples()), len(failures))
        self.assertTrue(all(f.startswith('leaks: ') for f in failures))

        class Unscrubbed(self.Manager):
            def sync_all(self, user, assistant):
                return user, assistant
        self.assertIn('unscrubbed hand-off: sync_all', probe.check(lambda t: '[redacted]', Unscrubbed))

        class DocstringOnly(self.Manager):
            def sync_all(self, user, assistant):
                """Goes through _redact_for_provider (it says)."""
                return user, assistant  # _redact_for_provider(user)
        self.assertIn('unscrubbed hand-off: sync_all', probe.check(lambda t: '[redacted]', DocstringOnly))

        def broken(text):
            raise RuntimeError
        self.assertIn('raises: jwt', probe.check(broken, self.Manager))

    def test_probe_covers_common_credential_shapes_without_committing_any(self):
        self.assertGreaterEqual(set(probe.samples()), {'openai-style key', 'github token', 'aws access key',
                                                        'bearer header', 'jwt', 'private key', 'env password',
                                                        'env api key'})
        source = (ROOT / 'deploy/hindsight/redaction_probe.py').read_text()
        for literal in ('sk-proj-', 'ghp_', 'AKIA', 'BEGIN PRIVATE KEY', 'eyJhbGci', 'PASSWORD='):
            self.assertNotIn(literal, source)  # assembled from fragments at runtime

    def test_deploy_refuses_unless_the_probe_prints_ok(self):
        image = 'nousresearch/hermes-agent@sha256:' + 'a' * 64
        for output, ok in (('redaction-probe: ok', True), ('warning\nredaction-probe: ok', True),
                           ('redaction-probe: failed: leaks: jwt', False), ('', False),
                           ('redaction-probe: ok\nredaction-probe: failed', False)):
            calls = []

            def run(argv):
                calls.append(argv)
                return output
            if ok:
                deployment.probe_hermes_redaction(ROOT, image, run)
            else:
                with self.assertRaisesRegex(ValueError, 'scrub secrets'):
                    deployment.probe_hermes_redaction(ROOT, image, run)
            argv = calls[0]
            self.assertEqual(['docker', 'run', '--rm', '--network', 'none', '--user', '10000'], argv[:7])
            self.assertEqual([image, '-c', (ROOT / 'deploy/hindsight/redaction_probe.py').read_text()], argv[-3:])


def _redact_for_provider(*values):
    return values


def _redact_messages_for_egress(messages):
    return messages


if __name__ == '__main__':
    unittest.main()
