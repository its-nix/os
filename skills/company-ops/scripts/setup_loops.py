#!/usr/bin/env python3
"""Idempotently install the LNX company loops as native Hermes cron jobs (default profile).

Creates missing jobs by name and leaves existing ones untouched; `--dry-run` prints the plan.
The health job runs the deployment-owned copy of health.py from $HERMES_HOME/scripts.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

JOBS = [
    {'name': 'nix-company-health', 'schedule': 'every 6h',
     'argv': ['--no-agent', '--script', 'nix-os/health.py', '--deliver', 'discord']},
    {'name': 'nix-founder-digest', 'schedule': '0 8 * * *',
     'argv': ['--skill', 'company-ops', '--deliver', 'discord'],
     'prompt': 'Founder digest: follow the company-ops skill. Outcomes from source systems only; one short message.'},
    {'name': 'nix-improvement', 'schedule': '0 5 * * 1',
     'argv': ['--skill', 'company-ops', '--skill', 't3-delegate', '--deliver', 'local'],
     'prompt': 'Weekly NixKit+T3 improvement loop: follow the company-ops skill. At most one evidence-backed change, metric fixed first.'},
]


def existing(home):
    store = home / 'cron' / 'jobs.json'
    if not store.exists():
        return set()
    data = json.loads(store.read_text())
    return {j.get('name') for j in (data.get('jobs', []) if isinstance(data, dict) else data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    have = existing(home)
    for job in JOBS:
        if job['name'] in have:
            print(f'present: {job["name"]}')
            continue
        cmd = [os.environ.get('HERMES_BIN', 'hermes'), 'cron', 'create', job['schedule']]
        if job.get('prompt'):
            cmd.append(job['prompt'])
        cmd += ['--name', job['name'], '--catch-up', 'off', *job['argv']]
        if args.dry_run:
            print('plan: ' + ' '.join(cmd[1:6]) + ' …')
            continue
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(f'failed: {job["name"]}', file=sys.stderr)
            return 1
        print(f'created: {job["name"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
