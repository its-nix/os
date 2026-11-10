#!/usr/bin/env python3
"""Validate a full native OS snapshot and restore only into an empty destination."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovery_db', HERE / 'recovery.py')
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
Refusal, digest = db.Refusal, db.digest
INERT = db.INERT_NAMES | {'postmaster.pid', '.backup.lock'}


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise Refusal('Unsafe snapshot path')
    return path


def safe_link(name, target):
    path = safe_name(name)
    link = PurePosixPath(target)
    if '\\' in target or '..' in link.parts and link.is_absolute():
        raise Refusal('Unsafe snapshot link')
    if link.is_absolute():
        # The KB virtualenv deliberately uses the exact pinned Hermes interpreter.
        if not (name.startswith('hermes/nix/kb/.venv/bin/') and target.startswith('/opt/hermes/')):
            raise Refusal('External state link is outside the pinned runtime')
        return
    parts = list(path.parent.parts)
    for part in link.parts:
        if part == '..':
            if not parts:
                raise Refusal('Snapshot link escapes state')
            parts.pop()
        elif part != '.':
            parts.append(part)


def snapshot(root, archive):
    root = Path(root)
    inventory = {}
    with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT, dereference=False) as tf:
        for path in sorted(root.rglob('*')):
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            if path.name in INERT or stat.S_ISSOCK(info.st_mode):
                continue
            safe_name(rel)
            member = tf.gettarinfo(str(path), arcname=rel)
            member.uname = member.gname = ''
            record = {'mode': stat.S_IMODE(info.st_mode) & 0o777, 'uid': info.st_uid, 'gid': info.st_gid}
            if path.is_symlink():
                target = os.readlink(path)
                safe_link(rel, target)
                record.update(type='link', target=target)
                tf.addfile(member)
            elif path.is_dir():
                record.update(type='directory')
                tf.addfile(member)
            elif path.is_file():
                record.update(type='file', size=info.st_size, sha256=digest(path))
                # Materialize regular hard-linked files; archive hard links are never followed.
                member.type, member.linkname, member.size = tarfile.REGTYPE, '', info.st_size
                with path.open('rb') as data:
                    tf.addfile(member, data)
            else:
                raise Refusal('Unsupported special file in durable state')
            inventory[rel] = record
    require_state(inventory)
    return inventory


def require_state(inventory):
    required = ['hermes/config.yaml', 'paperclip/instances/default/db/PG_VERSION']
    if any(inventory.get(name, {}).get('type') != 'file' for name in required):
        raise Refusal('Snapshot lacks required native state')
    if not any(name.startswith('paperclip/instances/default/secrets/') and row.get('size', 0) > 0
               for name, row in inventory.items()):
        raise Refusal('Snapshot lacks Paperclip encryption keys')
    for name in inventory:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and inventory.get(str(parent), {}).get('type') != 'directory':
                raise Refusal('Snapshot has a missing or non-directory ancestor')


def restore(archive, manifest, destination, *, preserve_owner=True):
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise Refusal('Restore destination must be empty')
    if digest(archive) != manifest['archive_sha256']:
        raise Refusal('Snapshot archive digest mismatch')
    inventory = manifest['files']
    require_state(inventory)
    with tarfile.open(archive, 'r:gz') as tf:
        members = tf.getmembers()
        if len({m.name for m in members}) != len(members) or {m.name for m in members} != set(inventory):
            raise Refusal('Snapshot coverage differs from manifest')
        for member in members:
            safe_name(member.name)
            row = inventory[member.name]
            expected_type = 'directory' if member.isdir() else 'file' if member.isfile() else 'link' if member.issym() else None
            if row.get('type') != expected_type or expected_type is None:
                raise Refusal('Unsupported or inconsistent snapshot member')
            if member.issym():
                safe_link(member.name, member.linkname)
                if row.get('target') != member.linkname:
                    raise Refusal('Snapshot link differs from manifest')
            if (member.mode & 0o777, member.uid, member.gid) != (row['mode'], row['uid'], row['gid']):
                raise Refusal('Snapshot ownership or permissions differ')
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for member in members:
            row = inventory[member.name]
            target = destination / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif member.issym():
                target.symlink_to(member.linkname)
            else:
                with tf.extractfile(member) as data, target.open('xb') as output:
                    shutil.copyfileobj(data, output)
                if target.stat().st_size != row['size'] or digest(target) != row['sha256']:
                    raise Refusal('Restored file integrity failed')
        # Apply directory permissions last, so restrictive original owners cannot block extraction.
        for name in sorted(inventory, key=lambda n: len(PurePosixPath(n).parts), reverse=True):
            row, target = inventory[name], destination / name
            if preserve_owner:
                os.chown(target, row['uid'], row['gid'], follow_symlinks=False)
            if row['type'] != 'link':
                target.chmod(row['mode'])
        for name, row in inventory.items():
            if row['type'] == 'file' and Path(name).suffix in ('.db', '.sqlite', '.sqlite3'):
                db.check_database((destination / name).resolve())
    return len(inventory)


def restore_bundle(payload, destination):
    manifest = json.loads((payload / 'manifest.json').read_text())
    if digest(payload / 'deployment.json') != manifest['deployment_sha256']:
        raise Refusal('Deployment metadata digest mismatch')
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise Refusal('Restore destination must be empty')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    count = restore(payload / 'snapshot.tar.gz', manifest, destination / 'state')
    model = json.loads((payload / 'deployment.json').read_text())
    for service in model['services'].values():
        for volume in service.get('volumes', []):
            if volume.get('type') != 'bind':
                raise Refusal('Unexpected deployment volume')
            source = Path(volume['source'])
            try:
                rel = source.relative_to(manifest['state_root'])
            except ValueError:
                raise Refusal('Deployment bind is outside captured state') from None
            # Compose resolves this relative to the exported file on the NEW host, not the
            # recovery container's /restore mount point.
            volume['source'] = './state/' + rel.as_posix()
    (destination / 'compose.json').write_text(json.dumps(model, indent=2) + '\n')
    (destination / 'compose.json').chmod(0o600)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'manifest.json').chmod(0o600)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'restore'], nargs='?', default='verify')
    parser.add_argument('--payload', type=Path, default=Path('/opt/nix-recovery'))
    parser.add_argument('--destination', type=Path, default=Path('/restore'))
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            with tempfile.TemporaryDirectory(prefix='nixos-restore-') as td:
                count = restore_bundle(args.payload, Path(td) / 'restored')
        else:
            count = restore_bundle(args.payload, args.destination)
        print(f'Full OS {args.command} passed: {count} entries; no services started.')
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError, RuntimeError):
        print('Full OS recovery failed; private diagnostics suppressed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
