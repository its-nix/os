#!/usr/bin/env python3
"""Prepare fresh native homes for the approved clean pilot; preview by default."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]


class Refusal(ValueError):
    pass


def inputs():
    versions = json.loads((REPO / 'deploy/versions.json').read_text())
    pilot = json.loads((REPO / 'deploy/pilot.json').read_text())
    official = {'hermes': 'nousresearch/hermes-agent', 'paperclip': 'ghcr.io/paperclipai/paperclip'}
    for name, repository in official.items():
        if not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', versions[name]['image']):
            raise Refusal('An immutable official image digest is required')
    return versions, pilot


def download_roles(versions):
    agency = versions['agency']
    result = {}
    for name, item in agency['files'].items():
        url = f"https://raw.githubusercontent.com/{agency['repository']}/{agency['revision']}/{item['path']}"
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(1024 * 1024)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise Refusal('Agency source digest mismatch')
        result[name] = data.decode('utf-8')
    return result


def write(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def prepare(root, roles=None):
    versions, pilot = inputs()
    root = Path(root).expanduser()
    if root.is_symlink() or root.exists():
        raise Refusal('Destination already exists; preparation never replaces state')
    root = root.resolve()
    if root.is_relative_to(REPO) or REPO.is_relative_to(root):
        raise Refusal('Deployment state must be outside the source checkout')
    roles = download_roles(versions) if roles is None else roles
    root.mkdir(mode=0o700, parents=True)
    home = root / 'hermes'
    knowledge = f"""
## Company knowledge access

Read `/knowledge/company/START-HERE.md`, then its `AGENTS.md` and only the relevant notes/policies. This is the immutable, read-only company snapshot, not an artifacts or runtime-memory store. Its schema/lifecycle governs contributions; the Nix role boundaries above carry the approved clean task-board routing.

Retrieve current evidence with canonical snapshot citations:

```bash
/opt/data/nix/kb/.venv/bin/python /knowledge/company/scripts/retrieve.py '<question>' --ref {versions['knowledgebase']['revision']}
/opt/data/nix/kb/.venv/bin/python /knowledge/company/scripts/retrieve.py --curate
```

No current result means no approved evidence found. Curation reports issues and never promotes or rewrites knowledge. Route knowledge-impact proposals through the lead and human review. Non-secret source pins are available at `/opt/data/nix/versions.json`. Do not install tools or seek broader credentials to bypass this read-only boundary.
"""
    for agent in pilot['agents']:
        profile = agent['profile']
        target = home if profile == 'default' else home / 'profiles' / profile
        boundaries = (REPO / 'deploy/roles' / (agent['id'] + '.md')).read_text()
        config = {
            'gateway': {'multiplex_profiles': True},
            'approvals': {'mode': 'manual', 'cron_mode': 'deny'},
            'terminal': {'cwd': '/opt/data/workspaces/' + profile},
        }
        write(target / 'config.yaml', json.dumps(config, indent=2) + '\n')
        write(target / 'SOUL.md', boundaries + knowledge + '\n## Agency role reference\n\n' + roles[agent['id']])
        write(target / '.env', 'API_SERVER_KEY=' + secrets.token_hex(32) + '\n')
        (home / 'workspaces' / profile).mkdir(mode=0o700, parents=True)
    write(home / 'nix/licenses/agency-LICENSE', roles['license'])
    write(home / 'nix/versions.json', json.dumps(versions, indent=2) + '\n')
    (root / 'paperclip').mkdir(mode=0o700)
    (root / 'knowledge').mkdir(mode=0o700)
    environment = {
        'NIX_CLEAN_ROOT': str(root), 'HERMES_IMAGE': versions['hermes']['image'],
        'PAPERCLIP_IMAGE': versions['paperclip']['image'],
    }
    # JSON quoting is compatible with Compose's double-quoted env-file values.
    write(root / 'deployment.env', ''.join(f'{k}={json.dumps(v)}\n' for k, v in environment.items()))
    write(root / 'versions.json', json.dumps(versions, indent=2) + '\n')
    print('Fresh pilot homes prepared; no service or unattended work was started.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/srv/nix-clean'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if not args.apply:
        inputs()
        print('Preview: prepare fresh Hermes/Paperclip homes, distinct profile keys and pinned roles. No writes.')
        return 0
    try:
        prepare(args.root)
        return 0
    except (OSError, ValueError):
        print('Preparation refused: destination must be new and source inputs valid; existing state is preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
