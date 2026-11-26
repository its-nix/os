"""Create a detached worktree with the selected additions to global core."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

TOOLS = Path(__file__).resolve().parent


def create(repository, path, revision, packs):
    manifest = json.loads((TOOLS / 'packs.json').read_text())['packs']
    additions = sorted({name for pack in packs for name in manifest[pack]} - set(manifest['core']))
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    commit = subprocess.check_output(['git', '-C', repository, 'rev-parse', '--verify',
                                      '--end-of-options', revision + '^{commit}'], text=True).strip()
    subprocess.run(['git', '-C', repository, 'worktree', 'add', '--detach', path, commit], check=True)
    # An unsuccessful preparation leaves the new worktree for inspection. Never remove user files.
    for relative in ['.agents', '.claude', '.opencode', '.codex']:
        parent = path / relative
        if parent.is_symlink() or any(os.path.lexists(parent / name) for name in ('skill', 'skills')):
            raise ValueError('New worktree already declares a harness skill destination')
    if additions:
        target = path / '.agents/skills'
        target.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.yaml'
            config.write_text(json.dumps({'source': str(TOOLS.parent), 'targets': {
                'project': {'path': str(target), 'mode': 'copy', 'include': additions}}}))
            subprocess.run([TOOLS / 'skillshare', 'sync', '--global', '--json'], cwd=tmp, check=True,
                           env={'HOME': tmp, 'PATH': os.defpath, 'SKILLSHARE_CONFIG': str(config)})
        if {p.name for p in target.iterdir()} != set(additions) | {'.skillshare-manifest.json'}:
            raise ValueError('Incomplete project selection')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('path', type=Path)
    parser.add_argument('revision')
    roles = sorted(json.loads((TOOLS / 'packs.json').read_text())['packs'].keys() - {'core'})
    parser.add_argument('--role', choices=roles)
    parser.add_argument('--task-pack', choices=roles, action='append', default=[])
    args = parser.parse_args()
    create(args.repository.resolve(), args.path.absolute(), args.revision,
           ([args.role] if args.role else []) + args.task_pack)
