#!/usr/bin/env python3
"""Validate and restore a full Hermes backup without starting any services."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile

# Machine-local process markers must never revive on another host.
INERT_NAMES = {'gateway_state.json', 'gateway.pid', 'cron.pid', 'gateway.lock', 'processes.json'}


class Refusal(ValueError):
    pass


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_database(path):
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise Refusal('Database integrity check failed')
    except sqlite3.DatabaseError:
        raise Refusal('Database integrity check failed') from None


def inspect_archive(archive):
    """Return hashes of every regular member; reject unsafe or incomplete input."""
    members = {}
    seen = set()
    with zipfile.ZipFile(archive) as zf, tempfile.TemporaryDirectory(prefix='hermes-db-check-') as td:
        for info in zf.infolist():
            path = PurePosixPath(info.filename)
            if (not info.filename or path.is_absolute() or '\\' in info.filename
                    or any(p in {'', '.', '..'} for p in info.filename.rstrip('/').split('/'))
                    or ':' in info.filename or path.as_posix() != info.filename.rstrip('/')):
                raise Refusal('Unsafe archive path')
            if path.as_posix() in seen:
                raise Refusal('Duplicate archive path')
            seen.add(path.as_posix())
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise Refusal('Non-regular archive member')
            if info.is_dir():
                continue
            if kind == stat.S_IFDIR or info.flag_bits & 1:
                raise Refusal('Unsupported archive member')
            with zf.open(info) as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()  # also checks ZIP CRC
            if path.suffix in {'.db', '.sqlite', '.sqlite3'}:
                scratch = Path(td) / 'check.db'
                with zf.open(info) as source, scratch.open('wb') as target:
                    shutil.copyfileobj(source, target)
                check_database(scratch)
                scratch.unlink()
            members[info.filename] = sha
        if not members or not any(PurePosixPath(n).name == 'config.yaml' for n in members):
            raise Refusal('Archive lacks Hermes configuration')
        # Ambiguous file/directory collisions must be rejected before touching the destination.
        for name in seen:
            if any(str(p) in members for p in PurePosixPath(name).parents if str(p) != '.'):
                raise Refusal('Archive path collision')
    return members


def restore(archive, destination):
    members = inspect_archive(archive)
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise Refusal('Restore destination must be empty')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive child creation also prevents two restores sharing a destination.
    (destination / 'hermes').mkdir(mode=0o700)
    (destination / 'user').mkdir(mode=0o700)
    restored = 0
    with zipfile.ZipFile(archive) as zf:
        for name, sha in members.items():
            external = name.startswith('_external/')
            if not external and (PurePosixPath(name).name in INERT_NAMES
                                 or name.endswith(('.db-wal', '.db-shm', '.db-journal'))):
                continue
            rel = name[len('_external/'):] if external else name
            target = destination / ('user' if external else 'hermes') / rel
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = 0o700 if zf.getinfo(name).external_attr >> 16 & 0o111 else 0o600
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, 'wb') as output, zf.open(name) as source:
                shutil.copyfileobj(source, output)
            if digest(target) != sha:
                raise Refusal('Restore readback failed')
            if target.suffix in {'.db', '.sqlite', '.sqlite3'}:
                check_database(target.resolve())
            restored += 1
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'restore'], nargs='?', default='verify')
    parser.add_argument('--archive', type=Path, default=Path('/opt/nix-recovery/snapshot.zip'))
    parser.add_argument('--manifest', type=Path, default=Path('/opt/nix-recovery/manifest.json'))
    parser.add_argument('--destination', type=Path, default=Path('/restore'))
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        if digest(args.archive) != manifest['archive_sha256']:
            raise Refusal('Archive digest mismatch')
        if args.command == 'verify':
            with tempfile.TemporaryDirectory(prefix='hermes-canary-') as td:
                count = restore(args.archive, Path(td) / 'restored')
        else:
            count = restore(args.archive, args.destination)
        print(f'Recovery {args.command} passed: {count} files checked; no services started.')
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError):
        # Member names, configs and subprocess diagnostics may contain private data.
        print('Recovery failed; archive invalid or destination unavailable/nonempty.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
