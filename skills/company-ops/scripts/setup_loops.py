#!/usr/bin/env python3
"""Idempotently install the LNX company loops as native Hermes cron jobs (default profile).

Creates missing jobs by name. `--reconcile` upgrades an existing digest in place.
The health and digest jobs use deployment-owned copies under $HERMES_HOME/scripts.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

DIGEST = 'nix-founder-digest'
DIGEST_SCRIPT = 'nix-os/digest.py'

JOBS = [
    {'name': 'nix-company-health', 'schedule': 'every 6h',
     'argv': ['--no-agent', '--script', 'nix-os/health.py', '--deliver', 'discord']},
    {'name': DIGEST, 'schedule': '0 8 * * *',
     'argv': ['--no-agent', '--script', DIGEST_SCRIPT, '--deliver', 'discord']},
    {'name': 'nix-improvement', 'schedule': '0 5 * * 1',
     'argv': ['--skill', 'company-ops', '--skill', 't3-delegate', '--deliver', 'local'],
     'prompt': 'Weekly NixKit+T3 improvement loop: follow the company-ops skill. At most one evidence-backed change, metric fixed first.'},
]


def existing(home):
    store = home / 'cron' / 'jobs.json'
    if not store.exists():
        return {}
    data = json.loads(store.read_text())
    return {j.get('name'): j for j in (data.get('jobs', []) if isinstance(data, dict) else data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--reconcile', action='store_true', help='Upgrade the founder digest without rescheduling it')
    args = parser.parse_args(argv)
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    have = existing(home)
    for job in JOBS:
        name = job['name']
        current = have.get(name)
        # Explicit profile: a sticky `hermes profile use` must not redirect LNX's loops.
        cmd = [os.environ.get('HERMES_BIN', 'hermes'), '-p', 'default', 'cron']
        if current is not None:
            if not (args.reconcile and name == DIGEST
                    and (not current.get('no_agent') or current.get('script') != DIGEST_SCRIPT)):
                print(f'present: {name}')
                continue
            cmd += ['edit', current['id'], '--no-agent', '--script', DIGEST_SCRIPT, '--clear-skills']
        else:
            cmd += ['create', job['schedule']]
            if job.get('prompt'):
                cmd.append(job['prompt'])
            cmd += ['--name', name, '--catch-up', 'off', *job['argv']]
        if args.dry_run:
            print(f'plan: {cmd[4]} {name}')
            continue
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(f'failed: {job["name"]}', file=sys.stderr)
            return 1
        print(f'{"updated" if current is not None else "created"}: {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
