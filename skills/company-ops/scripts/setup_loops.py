#!/usr/bin/env python3
"""Idempotently install the LNX company loops as native Hermes cron jobs (default profile).

Creates missing jobs by name; `--dry-run` prints the plan. Existing jobs are left untouched except that a
job declaring an idle-gate `script` gets exactly that one field reconciled (`cron edit --script`).
The health job runs the deployment-owned copy of health.py from $HERMES_HOME/scripts; the improvement job's
pre-run gate (`nix-os/gate-improvement.py`, installed by the deployment) skips the agent when idle.
Run it after a deployment: until the gate file exists the job is created or left ungated.
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
    {'name': 'nix-improvement', 'schedule': '0 5 * * 1', 'script': 'nix-os/gate-improvement.py',
     'argv': ['--skill', 'company-ops', '--skill', 't3-delegate', '--deliver', 'local'],
     'prompt': ('Weekly NixKit+T3 improvement loop: follow the company-ops skill and the evidence bundle above. '
                'At most one evidence-backed change, metric fixed first. Deterministic-first: a recurring correction '
                '(>=2 examples) gets a script/CI check/lint first, skill prose only if impossible. PR via T3 only; '
                'never edit the tests or evaluator that judge your own change.')},
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
    args = parser.parse_args(argv)
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    have = existing(home)
    for job in JOBS:
        hermes = [os.environ.get('HERMES_BIN', 'hermes'), '-p', 'default']
        # Hermes refuses a script that is not installed yet (the deployment installs it): run after a deploy.
        script = job.get('script') if job.get('script') and (home / 'scripts' / job['script']).is_file() else None
        if job.get('script') and not script:
            print(f'gate script not installed yet, ungated for now: {job["name"]}')
        if job['name'] in have:
            current = have[job['name']]
            if script and current.get('script') != script:
                if args.dry_run:
                    print(f'plan: script {job["name"]}')
                    continue
                edit = subprocess.run(hermes + ['cron', 'edit', str(current.get('id')), '--script', script],
                                      capture_output=True, text=True, timeout=120)
                if edit.returncode:
                    print(f'failed: {job["name"]}', file=sys.stderr)
                    return 1
                print(f'updated script: {job["name"]}')
                continue
            print(f'present: {job["name"]}')
            continue
        # Explicit profile: a sticky `hermes profile use` must not redirect LNX's loops.
        cmd = hermes + ['cron', 'create', job['schedule']]
        if job.get('prompt'):
            cmd.append(job['prompt'])
        cmd += ['--name', job['name'], '--catch-up', 'off', *job['argv']]
        if script:
            cmd += ['--script', script]
        if args.dry_run:
            print('plan: ' + ' '.join(cmd[1:8]) + ' …')
            continue
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(f'failed: {job["name"]}', file=sys.stderr)
            return 1
        print(f'created: {job["name"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
