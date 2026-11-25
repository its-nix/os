"""Inventory a read-only native home and return missing core skill mounts."""
import json
import os
from pathlib import Path
import re
import sys

from build import tree

ROOTS = {
    'hermes': ['skills'],
    't3': ['.agents/skills', '.codex/skills', '.claude/skills', '.config/opencode/skills',
           '.config/opencode/skill', '.gemini/antigravity-cli/skills'],
}


def check_dir(home, path):
    parent = home
    for part in path.relative_to(home).parts:
        parent = parent / part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError('Unsafe native skills directory')


def fail(error):
    raise error


def missing(home, core, service):
    names = set(json.loads((core / '.nixkit/packs.json').read_text())['packs']['core'])
    found = set()
    for relative in ROOTS[service]:
        root = home / relative
        check_dir(home, root)
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, onerror=fail):
            directory = Path(directory)
            if any((directory / child).is_symlink() for child in dirs + files):
                raise ValueError('Symlink in native skills inventory')
            if 'SKILL.md' not in files:
                continue
            manifest = directory / 'SKILL.md'
            if not manifest.is_file():
                raise ValueError('Invalid skill manifest')
            lines = manifest.read_text().splitlines()
            header = lines[1:lines.index('---', 1)] if lines and lines[0] == '---' else []
            declared = [line[5:].strip().strip('"\'') for line in header if line.startswith('name:')]
            if len(declared) != 1 or not re.fullmatch(r'[\w.-]+', declared[0]):
                raise ValueError('Unrecognized native skill identity')
            name = declared[0]
            if name in names:
                if name in found or tree(directory) != tree(core / name):
                    raise ValueError(f'Existing skill conflicts with locked copy: {name}')
                if service == 't3' and relative != '.agents/skills':
                    raise ValueError(f'Existing skill at another harness destination: {name}')
                found.add(name)
    root = 'skills/nixkit' if service == 'hermes' else '.agents/skills'
    result = []
    for name in sorted(names - found):
        path = home / root / name
        check_dir(home, path)
        # Docker leaves an empty mountpoint in the underlying home after detach.
        if path.exists() and any(path.iterdir()):
            raise ValueError(f'Occupied native mountpoint: {name}')
        result.append(name)
    return result


if __name__ == '__main__':
    print(json.dumps(missing(Path('/native'), Path('/core'), sys.argv[1])))
