"""Hindsight memory (PRD 7.4): pinned local server, one bank per profile, never a shared or cloud bank."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment_memory', ROOT / 'scripts/deploy-os.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
deployment.HINDSIGHT_UID, deployment.HINDSIGHT_GID = os.getuid(), os.getgid()
probe_spec = importlib.util.spec_from_file_location('redaction_probe', ROOT / 'deploy/hindsight/redaction_probe.py')
probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(probe)
CATALOG = (f'name: hindsight\nrepo: https://github.com/vectorize-io/hindsight\nsha: {deployment.HINDSIGHT_PLUGIN_SHA}\n'
           'subdir: hindsight-integrations/hermes\nversion: "1.2.4"\n')


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
        if argv[-1] == '/opt/data/profiles/demo/.env' and 'dotenv_values' in argv[-2]:
            return 'true'
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
            (plugin / '__init__.py').write_bytes(b'# reviewed fixture')
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
        manifest = Path(td) / 'reviewed-files.json'
        manifest.write_text(json.dumps({'__init__.py': hashlib.sha256(b'# reviewed fixture').hexdigest()}))
        patched = patch.object(deployment, 'HINDSIGHT_PLUGIN_FILES', manifest)
        patched.start()
        self.addCleanup(patched.stop)
        return state

    def key(self, state):
        return deployment.hindsight_api_key(state)

    def test_preflight_sets_bytecode_policy_for_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td, ())
            run = Mock(return_value='[null, false]')
            deployment.preflight_memory(ROOT, state, 'candidate', run)
            argv = run.call_args.args[0]
            self.assertIn('-B', argv[argv.index('candidate') + 1:argv.index('-c')])

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
                                          '--no-deps', '--no-enable'] for c in installs))
            # The catalog pin is read once, before the first install.
            reads = [i for i, c in enumerate(hermes.calls) if c[5:6] == ['cat']]
            self.assertEqual(1, len(reads))
            self.assertLess(reads[0], hermes.calls.index(installs[0]))
            self.assertTrue(all(c[:5] == ['docker', 'exec', '-u', '10000', 'nixos-hermes'] for c in hermes.calls))
            self.assertFalse((state / 'hermes/.hindsight').exists())  # never the shared fallback
            self.assertFalse(list(state.glob('hindsight-enable/*')))

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

    def test_changed_plugin_bytes_with_valid_catalog_refuse_activation(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td, ())
            hermes = FakeHermes(state, fail_set=True)
            with self.assertRaises(subprocess.CalledProcessError):
                deployment.configure_memory(state, hermes)
            plugin = state / 'hermes/plugins/hindsight'
            (plugin / '__init__.py').write_text('# changed after install')
            retry = FakeHermes(state)
            with self.assertRaisesRegex(ValueError, 'contents differ'):
                deployment.configure_memory(state, retry)
            self.assertFalse(any('set' in call for call in retry.calls))
            (plugin / '__init__.py').write_bytes(b'# reviewed fixture')
            (plugin / 'unexpected.pyc').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError, 'contents differ'):
                deployment.check_installed_plugin(state / 'hermes')

    def test_interrupted_activation_is_finished_not_mistaken_for_memory_off(self):
        with tempfile.TemporaryDirectory() as td:
            state = self.state(td, ('engineer',))
            hermes = FakeHermes(state, fail_set=True)
            with self.assertRaises(subprocess.CalledProcessError):
                deployment.configure_memory(state, hermes)
            self.assertTrue((state / 'hindsight-enable/default').exists())
            again = FakeHermes(state, providers=hermes.providers)
            deployment.configure_memory(state, again)
            self.assertEqual('hindsight', again.providers['default'])
            self.assertEqual('hindsight', again.providers['engineer'])
            self.assertFalse((state / 'hindsight-enable/default').exists())

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
            (state / 'hermes/profiles/demo/.env').write_text('OPENAI_KEY=x\nexport HINDSIGHT_BANK_ID=lnx\n')
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


class RedactionProbeTests(unittest.TestCase):
    """deploy/hindsight/redaction_probe.py runs inside the Hermes image before every rollout."""

    def test_probe_requires_delivery_and_checks_nested_arguments(self):
        manager = Mock()
        provider = None

        def register(recording_provider):
            nonlocal provider
            provider = recording_provider

        manager.add_provider.side_effect = register
        for source, destination in [('sync_all', 'sync_turn'), ('prefetch_all', 'prefetch'),
                                    ('queue_prefetch_all', 'queue_prefetch'), ('handle_tool_call', 'handle_tool_call'),
                                    ('on_memory_write', 'on_memory_write'), ('on_turn_start', 'on_turn_start'),
                                    ('on_session_end', 'on_session_end'), ('on_pre_compress', 'on_pre_compress'),
                                    ('on_delegation', 'on_delegation')]:
            getattr(manager, source).side_effect = lambda *a, hook=destination, **k: getattr(provider, hook)('masked')
        self.assertEqual([], probe.check(manager))
        # Simulate masking one field but forwarding the original transcript, plus a skipped callback.
        manager.sync_all.side_effect = lambda *a, **k: provider.sync_turn('masked', messages=k['messages'])
        manager.prefetch_all.side_effect = None
        failures = probe.check(manager)
        self.assertIn('missing delivery: prefetch', failures)
        for category in probe.samples():
            self.assertIn('leaks: sync_turn / ' + category, failures)

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


class HermesPathSafetyTests(unittest.TestCase):
    def test_linked_shared_directory_refused_even_if_target_exists_only_in_container(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / 'hermes').mkdir()
            (state / 'hermes/.hindsight').symlink_to('/opt/data/container-only')
            run = Mock(side_effect=AssertionError('unexpected container command'))
            with self.assertRaisesRegex(ValueError, 'Shared'):
                deployment.preflight_memory(ROOT, state, 'candidate', run)
            with self.assertRaisesRegex(ValueError, 'shared'):
                deployment.configure_memory(state, run)

    def test_root_never_follows_a_swapped_in_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            hermes, outside = Path(td) / 'hermes', Path(td) / 'outside'
            (hermes / 'profiles').mkdir(parents=True)
            outside.mkdir()
            (hermes / 'profiles' / 'engineer').symlink_to(outside)
            with self.assertRaises(ValueError):
                deployment._write_in_hermes(hermes, ['profiles', 'engineer', 'hindsight'], 'config.json', '{}', 0o600)
            self.assertEqual([], list(outside.iterdir()))
            (hermes / 'profiles' / 'real' / 'hindsight').mkdir(parents=True)
            (hermes / 'profiles' / 'real' / 'hindsight' / 'config.json').symlink_to(outside / 'victim')
            deployment._write_in_hermes(hermes, ['profiles', 'real', 'hindsight'], 'config.json', '{"a": 1}', 0o600)
            target = hermes / 'profiles' / 'real' / 'hindsight' / 'config.json'
            self.assertFalse(target.is_symlink())  # the link itself is replaced, never written through
            self.assertEqual('{"a": 1}', target.read_text())
            self.assertFalse((outside / 'victim').exists())


if __name__ == '__main__':
    unittest.main()
