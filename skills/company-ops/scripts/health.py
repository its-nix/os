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
import re
import shutil
import subprocess
import sys
import urllib.request

BACKUP_MAX_AGE = timedelta(hours=36)
DEPLOY_MAX_AGE = timedelta(hours=36)
DISK_MAX_USED = 0.90
BLOCKED_MAX_AGE = timedelta(hours=24)
VERSION = '1.1.0'
MISSING = object()
LIFECYCLES = ('approved', 'incubating', 'operating', 'scaling', 'pivoting', 'paused', 'retired')
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
LOCAL_APPROVAL = ('queued-local', 'submitting')
APPROVAL_STATES = LOCAL_APPROVAL + ('awaiting-founder', 'expired', 'closed')
BACKUP_STATES = ('running', 'published', 'verified_local', 'failed')


def read_state(path, *, missing=None):
    """Read one JSON object without exposing the input or parser errors."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return missing
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def valid_health(data):
    timestamps = ('backup_last_success', 'deployed_at')
    optional = ('backup_last_attempt_status', 'deploy_attempt_status', 'deploy_attempt_revision')
    return (data is not None and all(key in data for key in timestamps)
            and all(data.get(key) is None or isinstance(data[key], str) for key in (*timestamps, *optional)))


def parse(ts):
    if not isinstance(ts, str):
        return None
    try:
        value = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def check(home, now=None, kanban=None, t3_ok=None, disk=None):
    now = now or datetime.now(timezone.utc)
    problems = []
    status = home / 'nix' / 'health.json'
    data = read_state(status, missing=MISSING)
    if data is MISSING:
        problems.append('No deployment health status published (nix/health.json missing)')
    elif not valid_health(data):
        problems.append('Deployment health status unreadable or invalid (nix/health.json)')
    else:
        backup = parse(data['backup_last_success'])
        if not backup or now - backup > BACKUP_MAX_AGE:
            last_attempt = data.get('backup_last_attempt_status')
            attempt = last_attempt if last_attempt in BACKUP_STATES else 'unknown'
            problems.append(f'Recovery backup stale: last success {backup.isoformat() if backup else "unknown"}'
                            f' (last attempt: {attempt})')
        deployed = parse(data['deployed_at'])
        if not deployed or now - deployed > DEPLOY_MAX_AGE:
            problems.append(f'No healthy deployment since {deployed.isoformat() if deployed else "unknown"}')
        if data.get('deploy_attempt_status') == 'failed':
            revision = data.get('deploy_attempt_revision') or ''
            attempt = f' ({revision[:8]})' if re.fullmatch(r'[a-f0-9]{40}', revision) else ''
            problems.append(f'Latest deployment attempt failed{attempt}')
    used = disk if disk is not None else (lambda u: u.used / u.total)(shutil.disk_usage(home))
    if used > DISK_MAX_USED:
        problems.append(f'Host disk {used:.0%} used')
    if not (t3_ok if t3_ok is not None else t3_reachable()):
        problems.append('T3 Code is unreachable on 127.0.0.1:3773')
    registry = home / 'ventures'
    for manifest in sorted(registry.glob('*/manifest.json')) if registry.is_dir() else []:
        venture = read_state(manifest) or {}
        lifecycle = venture.get('lifecycle')
        usable = lifecycle in LIFECYCLES and all(
            isinstance(venture.get(key), str) and SLUG.fullmatch(venture[key]) for key in ('id', 'board')
        )
        vid = venture['id'] if usable else repr(manifest.parent.name[:64])
        if not usable:
            problems.append(f'{vid}: venture manifest unreadable or invalid')
        if lifecycle in ('paused', 'retired'):
            continue
        local = 0
        for path in sorted((manifest.parent / 'actions').glob('*.json')):
            record = read_state(path) or {}
            action_id, state = record.get('action_id'), record.get('status')
            if not isinstance(action_id, str) or not SLUG.fullmatch(action_id) or state not in APPROVAL_STATES:
                problems.append(f'{vid}: approval record {path.name[:69]!r} unreadable or invalid')
            elif state in LOCAL_APPROVAL:
                local += 1
        if local:
            problems.append(f'{vid}: {local} approval request(s) not delivered to the founder queue')
        if not usable:
            continue
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
