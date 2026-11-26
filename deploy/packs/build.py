#!/usr/bin/env python3
"""Compile one pack selection into a fresh copy target. Run as the build user."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
VERSION = '0.31.0'
ARCHIVES = {
    ('Linux', 'x86_64'): ('linux_amd64', 'a2943d5dfeeec0b626a71c710d2fc1dbbfac556386fdb8cf4b8b2f4485e837c0'),
    ('Darwin', 'arm64'): ('darwin_arm64', 'be98c287f7613fdd642c5f462f62e3b81b73a9353ea3508bb5937c175dd18c8d'),
}


def selection(manifest, packs):
    return sorted({skill for pack in packs for skill in manifest['packs'][pack]})


def skillshare(work):
    target, digest = ARCHIVES[(platform.system(), platform.machine())]
    filename = f'skillshare_{VERSION}_{target}.tar.gz'
    archive = work / filename
    urllib.request.urlretrieve(f'https://github.com/runkids/skillshare/releases/download/v{VERSION}/{filename}', archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('Skillshare release digest mismatch')
    binary = work / 'skillshare'
    with tarfile.open(archive) as package:
        entry = package.getmember('skillshare')
        if not entry.isfile():
            raise ValueError('Invalid release executable')
        binary.write_bytes(package.extractfile(entry).read())
    binary.chmod(0o755)
    return binary


def tree(root):
    files = {}
    for path in [root, *root.rglob('*')]:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Unexpected link or special entry in skill')
        files[path.relative_to(root)] = (path.stat().st_mode & 0o111 != 0,
                                         hashlib.sha256(path.read_bytes()).digest() if path.is_file() else None)
    return files


def build(checkout, destination, packs, runtime=False):
    manifest = json.loads((HERE / 'packs.json').read_text())
    names = selection(manifest, manifest['packs'] if runtime else packs)
    revision = json.loads((HERE.parent / 'versions.json').read_text())['kit']['revision']
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary).resolve()
        home = work / 'home'
        home.mkdir()
        env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'CI': 'true',
               'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '1',
               'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': str(checkout)}
        def run(*args):
            # Kit's tree digest includes modes set by checkout and installation.
            subprocess.run(args, env=env, cwd=work, check=True, umask=0o022)

        kit = work / 'kit'
        if checkout.is_file():
            # Deploy exports exactly the reviewed Git tree outside the build container.
            kit.mkdir()
            run('tar', 'xf', checkout, '-C', kit)
        else:
            run('git', 'clone', '--quiet', '--no-local', '--no-checkout', str(checkout), kit)
            run('git', '-C', kit, 'checkout', '--quiet', '--detach', revision)
        prefix = work / 'input'
        prefix.mkdir()
        run('node', kit / 'install.mjs', '--apply', '--skills', '--agent', 'hermes', '--prefix', prefix,
            *(arg for name in names for arg in ('--skill', name)))
        expected = {name: manifest['kit_lock']['skills'][name] for name in names}
        if json.loads((kit / 'skills-lock.json').read_text()) != {'version': 2, 'skills': expected}:
            raise ValueError('Fetched skill provenance or content differs from reviewed Kit lock')
        binary = skillshare(work)
        # Refuse existing targets even on retries. Callers publish only after successful return.
        destination.mkdir()
        config = work / 'config.yaml'
        config.write_text(json.dumps({'source': str(prefix / 'skills'), 'targets': {
            'pack': {'path': str(destination), 'mode': 'copy'}}}))
        env['SKILLSHARE_CONFIG'] = str(config)
        run(binary, 'sync', '--global', '--json')
        if {p.name for p in destination.iterdir()} != set(names) | {'.skillshare-manifest.json'}:
            raise ValueError('Incomplete copy target')
        for name in names:
            if tree(destination / name) != tree(prefix / 'skills' / name):
                raise ValueError(f'Skillshare copy differs for {name}')

        if runtime:
            tools = destination / '.nixkit'
            tools.mkdir()
            shutil.copy2(binary, tools / 'skillshare')
            for filename in ['packs.json', 'worktree.py']:
                shutil.copy2(HERE / filename, tools / filename)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    parser.add_argument('destination', type=Path)
    roles = sorted(json.loads((HERE / 'packs.json').read_text())['packs'].keys() - {'core'})
    parser.add_argument('--role', choices=roles)
    parser.add_argument('--runtime', action='store_true', help='Bundle all packs and the pinned pre-launch worktree installer')
    args = parser.parse_args()
    build(args.checkout.resolve(), args.destination.absolute(), ['core'] + ([args.role] if args.role else []), args.runtime)
