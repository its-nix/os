#!/usr/bin/env python3
"""Company health check: deterministic, no LLM. Prints ONLY problems (empty output = silent).

Runs as a Hermes no-agent cron job in the LNX profile. Inputs are native and read-only:
$HERMES_HOME/nix/health.json (published by the root deployment: deploy receipt and backup
recency), the venture registry, venture approval receipts, Kanban boards and the T3 endpoint.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

BACKUP_MAX_AGE = timedelta(hours=36)
DEPLOY_MAX_AGE = timedelta(hours=36)
DISK_MAX_USED = 0.90
BLOCKED_MAX_AGE = timedelta(hours=24)


def parse(ts):
    try:
        value = datetime.fromisoformat(ts.replace('Z', '+00:00')) if ts else None
    except (TypeError, ValueError):
        return None
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def check(home, now=None, kanban=None, t3_ok=None, disk=None):
    now = now or datetime.now(timezone.utc)
    problems = []
    status = home / 'nix' / 'health.json'
    if not status.exists():
        problems.append('No deployment health status published (nix/health.json missing)')
    else:
        data = json.loads(status.read_text())
        backup = parse(data.get('backup_last_success'))
        if not backup or now - backup > BACKUP_MAX_AGE:
            problems.append(f'Recovery backup stale: last success {data.get("backup_last_success") or "never"}'
                            f' (last attempt: {data.get("backup_last_attempt_status")})')
        deployed = parse(data.get('deployed_at'))
        if not deployed or now - deployed > DEPLOY_MAX_AGE:
            problems.append(f'No healthy deployment since {data.get("deployed_at") or "never"}')
        if data.get('deploy_attempt_status') == 'failed':
            problems.append(f'Latest deployment attempt failed ({str(data.get("deploy_attempt_revision"))[:8]})')
    used = disk if disk is not None else (lambda u: u.used / u.total)(shutil.disk_usage(home))
    if used > DISK_MAX_USED:
        problems.append(f'Host disk {used:.0%} used')
    if not (t3_ok if t3_ok is not None else t3_reachable()):
        problems.append('T3 Code is unreachable on 127.0.0.1:3773')
    registry = home / 'ventures'
    for manifest in sorted(registry.glob('*/manifest.json')) if registry.is_dir() else []:
        venture = json.loads(manifest.read_text())
        if venture['lifecycle'] in ('paused', 'retired'):
            continue
        waiting = [json.loads(p.read_text()) for p in sorted((manifest.parent / 'actions').glob('*.json'))]
        local = [w['action_id'] for w in waiting if w.get('status') in ('queued-local', 'submitting')]
        if local:
            problems.append(f'{venture["id"]}: {len(local)} approval request(s) not delivered to the founder queue')
        try:
            tasks = (kanban or board_tasks)(venture['board'])
        except Exception:  # one unreadable board never hides the other findings
            problems.append(f'{venture["id"]}: board {venture["board"]} unreadable')
            continue
        for task in tasks:
            since = task.get('blocked_since')
            if task.get('status') == 'blocked' and isinstance(since, (int, float)) \
                    and now - datetime.fromtimestamp(since, timezone.utc) > BLOCKED_MAX_AGE:
                problems.append(f'{venture["id"]}: card {task["id"]} blocked >24h: {task.get("title", "")[:80]}')
    return problems


def board_tasks(board):
    """Blocked cards with the time of their latest `blocked` event (not their creation time)."""
    def hermes(*argv):
        result = subprocess.run(['hermes', 'kanban', '--board', board, *argv, '--json'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(board)
        return json.loads(result.stdout or '[]')
    tasks = hermes('list', '--status', 'blocked')
    for task in tasks:
        events = hermes('show', task['id']).get('events', [])
        blocked = [e.get('created_at') for e in events if e.get('kind') == 'blocked']
        task['blocked_since'] = blocked[-1] if blocked else None
    return tasks


def t3_reachable():
    try:
        with urllib.request.urlopen('http://127.0.0.1:3773/.well-known/t3/environment', timeout=5) as r:
            return r.status == 200
    except OSError:
        return False


if __name__ == '__main__':
    found = check(Path(os.environ.get('HERMES_HOME', '/opt/data')))
    if found:
        print('Nix company health — needs attention:\n' + '\n'.join('• ' + p for p in found))
    sys.exit(0)
