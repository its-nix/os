#!/usr/bin/env python3
"""Opt-in real Kit exercise in a disposable synthetic home, NOT authorization.

The production receipt consumer is tested separately in temporary Git fixtures.
This harness deliberately substitutes synthetic authority in memory ONLY; it
cannot be invoked through either production CLI, never writes a receipt, and
never changes the supplied Kit snapshot or real profile. All installer writes
are directed at temporary copies. No network/package acquisition is allowed.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic-test-mode', action='store_true', required=True)
    parser.add_argument('--kit-snapshot', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('synthetic_exercise', ROOT / 'scripts/knowledge_execution.py')
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = m.load_packet()
    m.source_preflight(args.kit_snapshot.absolute(), p)  # read-only, exact audited source set
    node = m.cached_node()
    scanner = Path.home() / '.hermes/hermes-agent/tools/skills_guard.py'
    with tempfile.TemporaryDirectory(prefix='v2-kit-synthetic-') as td:
        base = Path(td)
        home = base / '.hermes'
        kit = base / 'kit'
        shutil.copytree(args.kit_snapshot, kit, symlinks=True)
        for name in p['install_targets']:
            if name == 'nix-knowledge':
                continue
            source = kit / ('skills' if name == 'watch' else '.agents/skills') / name
            shutil.copytree(source, home / 'skills' / name)
        for row in p['preservation']:
            root = home / 'skills' / row['path']
            root.mkdir(parents=True)
            (root / 'SKILL.md').write_text('---\nname: ' + row['skill'] + '\n---\nSYNTHETIC preserved fixture only.\n')
            row['tree_sha256'] = m.installer._skill_tree_digest(root)
        cache = home / 'skills/watch/scripts/__pycache__'
        cache.mkdir()
        cache.chmod(0o775)
        for name in m.CACHE_NAMES:
            target = cache / name
            target.write_bytes(b'SYNTHETIC CACHE ONLY: ' + name.encode())
            target.chmod(0o664)
        p['cache']['inventory'] = m.snapshot(home / 'skills/watch')[0]
        p['cache']['files'] = [r for r in p['cache']['inventory'] if r['path'].endswith('.pyc')]
        p['cache']['before_tree_sha256'] = m.installer._skill_tree_digest(home / 'skills/watch')
        manifest = m.approved_manifest(json.loads((ROOT / 'skills/packs.json').read_bytes()), p)
        scanner_copy = home / 'hermes-agent/tools/skills_guard.py'
        scanner_copy.parent.mkdir(parents=True)
        shutil.copyfile(scanner, scanner_copy)
        arguments = SimpleNamespace(profile='lnx', nixkit=kit,
            skill=manifest['approved_additions']['lnx'], apply=False,
            approval=[ROOT / 'skills/approvals' / (n + '.json') for n in
                ('lnx-caveman-d8ede5cf', 'lnx-watch-a9cb5b57', 'lnx-todoist-cli-340d7b21', 'lnx-nix-knowledge-1182c51c')])
        env = {k: v for k, v in os.environ.items() if not k.startswith('NIXKIT_TEST_')}
        env['HERMES_HOME'] = str(home)
        with mock.patch.object(m, 'consume_receipt', return_value=p), \
             mock.patch.object(m, 'validate_single_receipt'), \
             mock.patch.object(m, 'cached_node', return_value=node), \
             mock.patch.object(Path, 'home', return_value=base), \
             mock.patch.dict(os.environ, env, clear=True):
            before = m.snapshot(home / 'skills/watch')[0]
            assert m.production(arguments, manifest) == 0
            assert before == m.snapshot(home / 'skills/watch')[0]
            assert not (base / '.local').exists()
            arguments.apply = True
            # Inject a nonzero Kit process result, not a fake successful install.
            # Git, native scanner and all other subprocesses still really run.
            import subprocess
            real_run = subprocess.run
            def fail_kit(command, *a, **kw):
                if any(str(item).endswith('/install.mjs') for item in command) and '--apply' in command:
                    return subprocess.CompletedProcess(command, 7)
                return real_run(command, *a, **kw)
            with mock.patch.object(subprocess, 'run', side_effect=fail_kit):
                try:
                    m.production(arguments, manifest)
                except m.Refusal as exc:
                    assert 'Kit execution failed' in str(exc), str(exc)
                else:
                    raise AssertionError('synthetic Kit failure did not fail closed')
            assert before == m.snapshot(home / 'skills/watch')[0]
            assert not (home / 'skills/nix-knowledge').exists()
            assert m.production(arguments, manifest) == 0
            m.targets_preflight(home, p, cleaned=True, installed=True)
            scan = m.scan_knowledge(home / 'skills/nix-knowledge')
            assert m.production(arguments, manifest) == 0  # verified no-op
            state = base / '.local/state/nix-skill-rollbacks/lnx-knowledge-v2'
            transactions = list(state.iterdir())
            assert len(transactions) == 2  # failed/rolled-back AND completed archives survive
            by_status = {json.loads((q / 'journal.json').read_text())['state']: q for q in transactions}
            assert set(by_status) == {'rolled_back', 'completed'}
            retained_failure = m.snapshot(by_status['rolled_back'])[0]
            for transaction in transactions:
                assert {q.name for q in transaction.iterdir()} == {'journal.json', *m.CACHE_NAMES}
            with m.CacheTransaction(home, state, p, recovery=by_status['completed']) as tx:
                tx.rollback()
            assert before == m.snapshot(home / 'skills/watch')[0]
            assert retained_failure == m.snapshot(by_status['rolled_back'])[0]
            assert set(state.iterdir()) == set(transactions)
            print(json.dumps({'synthetic_home_only': True, 'real_kit_commit': m.installer.NIXKIT_COMMIT,
                'production_authorization_exercised': False, 'preview_pure': True,
                'install_targets': len(p['install_targets']), 'preserved': len(p['preservation']),
                'injected_kit_failure_rollback_exact': True, 'retained_archives': len(transactions),
                'cache_rollback_exact': True, 'repeat_verified_noop': True, 'native_guard': scan}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
