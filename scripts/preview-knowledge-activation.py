#!/usr/bin/env python3
"""Pure default-only pending-proposal diagnostic. NEVER executes an installer.

No fetch, cache publication, lock, directory creation, archive or apply route.
The production wrapper still requires separately authenticated approval.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / 'skills/activation-proposals/lnx-knowledge-v2.json'
PROPOSAL_SHA256 = '63e0706c4e262f4a4fe300fc040f2f70262b4f011261f87bd2362f8face270ab'
spec = importlib.util.spec_from_file_location('activation_installer', ROOT / 'scripts/install-skills.py')
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_proposal(value):
    def reject_runtime_state(item):
        if isinstance(item, dict):
            if {'identity', 'device', 'inode', 'uid', 'gid', 'mtime_ns', 'size'} & item.keys():
                raise installer.Refusal('runtime identity forbidden in durable proposal')
            for child in item.values():
                reject_runtime_state(child)
        elif isinstance(item, list):
            for child in item:
                reject_runtime_state(child)
    reject_runtime_state(value)
    if canonical_digest(value) != PROPOSAL_SHA256:
        raise installer.Refusal('pending proposal differs from exact reviewed source-bound document')
    if (value['profile'], value['hermes_profile'], value['status']) != ('lnx', 'default', 'pending_approval'):
        raise installer.Refusal('proposal is not pending default-only scope')


def load_proposal():
    value = installer._load_json(PROPOSAL, 'pending activation proposal')
    validate_proposal(value)
    return value


def identity(info):
    return {'device': info.st_dev, 'inode': info.st_ino, 'mode': stat.S_IMODE(info.st_mode),
            'uid': info.st_uid, 'gid': info.st_gid, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'type': stat.S_IFMT(info.st_mode)}


def inventory(root):
    """Return semantic rows only; live identities never leave this invocation.

    Non-following opens and ephemeral stat comparisons bracket reads and the
    snapshot. Future execution must capture fresh identities/ownership outside
    Git and use descriptor-relative operations, not this diagnostic as authority.
    """
    installer._check_no_symlink(root)
    before = installer._skill_tree_digest(root)
    rows = []
    live_identities = {}
    for path in [root] + sorted(root.rglob('*')):
        installer._check_no_symlink(path)
        info = path.lstat()
        observed = identity(info)
        live_identities[path] = observed
        row = {'path': path.relative_to(root).as_posix(),
               'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFMT(info.st_mode)}
        if stat.S_ISREG(info.st_mode):
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if identity(os.fstat(fd)) != observed:
                    raise installer.Refusal('inventory object replaced before open')
                h = hashlib.sha256()
                while data := os.read(fd, 1024 * 1024):
                    h.update(data)
                if identity(os.fstat(fd)) != observed or identity(path.lstat()) != observed:
                    raise installer.Refusal('inventory object changed during hashing')
                row['sha256'] = h.hexdigest()
            finally:
                os.close(fd)
        elif not stat.S_ISDIR(info.st_mode):
            raise installer.Refusal('inventory contains non-regular object')
        rows.append(row)
    if installer._skill_tree_digest(root) != before:
        raise installer.Refusal('inventory tree changed during snapshot')
    for path, observed in live_identities.items():
        if identity(path.lstat()) != observed:
            raise installer.Refusal('inventory object replaced during snapshot')
    return rows


def summary(p):
    return {'authority': 'NOT authorized production preview', 'status': p['status'],
            'profile': 'lnx', 'hermes_profile': 'default', 'live_validated': False,
            'effective_skills': p['effective_skills'], 'preservation': p['preservation'],
            'install_targets': p['install_targets'], 'cache': p['cache'],
            'remaining_gates': p['remaining_gates']}


def validate_live_targets(p, home):
    """Compare complete live closures, projecting only the exact approved-data plan.

    This is NOT a generalized hash exclusion and NEVER changes the tree. A later
    transaction must prove the actual post-cleanup tree equals the acquired one.
    """
    cache = p['cache']
    watch = home / 'skills/watch'
    if installer._skill_tree_digest(watch) != cache['before_tree_sha256'] or inventory(watch) != cache['inventory']:
        raise installer.Refusal('exact watch payload/cache semantic drift; re-proposal required')
    excluded = {row['path'] for row in cache['files']} | {cache['remove_directory_if_empty']}
    for name in p['install_targets']:
        root = home / 'skills' / name
        if name == 'nix-knowledge':
            if root.exists() or root.is_symlink():
                raise installer.Refusal('candidate destination already exists')
            continue
        rows = inventory(root)
        if name == 'watch':
            rows = [row for row in rows if row['path'] not in excluded]
        if rows != p['acquired_target_closures'][name]:
            raise installer.Refusal('non-overwrite target closure drift: ' + name)
    if (home / 'skills/nix-document-design').exists():
        raise installer.Refusal('task-only candidate unexpectedly present')


def live_preview(p, home, kit):
    validate_proposal(p)
    if home != Path.home() / ".hermes":
        raise installer.Refusal("pending proposal binds only the default home, not another profile/path")
    installer._check_no_symlink(home)
    installer._check_no_symlink(kit)
    if installer._run(['git', 'status', '--porcelain', '--untracked-files=all'], ROOT):
        raise installer.Refusal('diagnostic requires clean committed governance checkout')
    installer._canonical_manifest(installer._load_json(installer.CANONICAL_MANIFEST, 'manifest'))
    if hashlib.sha256(installer.CANONICAL_MANIFEST.read_bytes()).hexdigest() != p['manifest_sha256']:
        raise installer.Refusal('proposal manifest drift')
    if installer._run(['git', 'rev-parse', 'HEAD'], kit) != p['nixkit']['commit'] or installer._run(['git', 'status', '--porcelain', '--untracked-files=all'], kit):
        raise installer.Refusal('diagnostic Kit pin or clean-state mismatch')
    if hashlib.sha256((kit / 'sources.json').read_bytes()).hexdigest() != p['nixkit']['sources_sha256']:
        raise installer.Refusal('diagnostic Kit sources drift')
    catalog = installer._source_catalog(kit / 'sources.json')
    installer._verify_installer(kit)
    for relative, tree in installer.NIXKIT_LOCAL_TREES.items():
        installer._verify_local_tree(kit, relative, tree)
    if set(p['effective_skills']) - set(catalog):
        raise installer.Refusal('desired skills absent from pinned catalog')
    preserved = installer._validate_host_native_satisfaction('lnx', home, {'lnx': p['preservation']})
    installer._preflight_duplicate_names(home, set(p['install_targets']), preserved)
    proposal_file = ROOT / 'skills/proposals/lnx-nix-knowledge-1182c51c.json'
    if hashlib.sha256(proposal_file.read_bytes()).hexdigest() != p['skill_proposal_sha256']:
        raise installer.Refusal('single-skill proposal drift')
    installer._validate_proposal(installer._load_json(proposal_file, 'skill proposal'),
                                 'lnx-nix-knowledge-1182c51c', 'lnx', {'nix-knowledge'}, catalog)
    validate_live_targets(p, home)
    out = summary(p)
    out['live_validated'] = True
    out['downstream_no_overwrite'] = 'Full existing closures match acquired bytes/modes, conditional on exact cache-plan projection ONLY; actual cleanup/post-cleanup check and production preview remain unauthorized'
    out['planned_command_NOT_EXECUTED'] = ['npx', '--yes', 'node@' + installer.NODE_RUNTIME, str(kit / 'install.mjs'), '--apply', '--skills', '--agent', 'hermes', '--prefix', str(home)] + [arg for name in p['install_targets'] for arg in ('--skill', name)]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['lnx'], default='lnx')
    parser.add_argument('--live-read-only', action='store_true')
    parser.add_argument('--nixkit', type=Path)
    args = parser.parse_args()
    try:
        p = load_proposal()
        if args.live_read_only:
            if args.nixkit is None:
                raise installer.Refusal('--live-read-only requires exact --nixkit checkout')
            home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))).absolute()
            out = live_preview(p, home, args.nixkit.absolute())
        else:
            out = summary(p)
        print(json.dumps(out, indent=2))
        return 0
    except (installer.Refusal, OSError, ValueError) as exc:
        print('Refusing: ' + str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
