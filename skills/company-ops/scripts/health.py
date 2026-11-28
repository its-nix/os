#!/usr/bin/env python3
"""Company health check: deterministic, no LLM. Prints ONLY problems (empty output = silent).

Runs as a Hermes no-agent cron job in the LNX profile. Reads the host health snapshot,
Hermes cron and usage data, venture and approval records, Kanban boards and the T3 endpoint.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request

BACKUP_MAX_AGE = timedelta(hours=36)
DEPLOY_MAX_AGE = timedelta(hours=36)
DISK_MAX_USED = 0.90
BLOCKED_MAX_AGE = timedelta(hours=24)
APPROVAL_MAX_AGE = timedelta(minutes=15)
SNAPSHOT_MAX_AGE = timedelta(hours=2)
PAT_NOTICE_DAYS = 14
WEEK = timedelta(days=7)
VERSION = '1.2.0'
MISSING = object()
LIFECYCLES = ('approved', 'incubating', 'operating', 'scaling', 'pivoting', 'paused', 'retired')
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
LOCAL_APPROVAL = ('queued-local', 'submitting')
APPROVAL_STATES = LOCAL_APPROVAL + ('awaiting-founder', 'expired', 'closed')
BACKUP_STATES = ('running', 'published', 'verified_local', 'failed')
PROVIDERS = ('claude', 'codex', 'opencode', 'grok', 'antigravity')
SERVICES = ('hermes', 't3', 'hindsight', 'reviewer',
            'twenty-db', 'twenty-redis', 'twenty-server', 'twenty-worker')


def read_json(path, *, missing=None):
    """Read JSON without exposing the input or parser errors."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return missing
    except (OSError, ValueError, RecursionError):
        return None
    return data


def read_state(path, *, missing=None):
    data = read_json(path, missing=missing)
    return data if data is missing or isinstance(data, dict) else None


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


def snapshot_problems(data, now):
    problems = []
    if 'observed_at' in data:
        observed = parse(data['observed_at'])
        if not observed or abs(now - observed) > SNAPSHOT_MAX_AGE:
            problems.append('Host health snapshot stale or unavailable')
    if 'containers' in data:
        containers = data['containers']
        if not isinstance(containers, dict):
            problems.append('Container status unavailable')
        else:
            for service in SERVICES:
                if service not in containers:
                    continue
                status = containers[service]
                if status in ('healthy', 'running') or (service.startswith('twenty-') and status == 'disabled'):
                    continue
                if status not in ('created', 'restarting', 'removing', 'paused', 'exited', 'dead',
                                  'starting', 'unhealthy'):
                    status = 'unavailable'
                problems.append(f'Container {service} degraded ({status})')
    if 'approval_worker_last_success' in data:
        success = parse(data['approval_worker_last_success'])
        if not success or abs(now - success) > APPROVAL_MAX_AGE:
            when = success.isoformat() if success else 'unknown'
            problems.append(f'Approval worker stale: last success {when}')
    if 'pack_sync' in data:
        pack = data['pack_sync']
        synced = parse(pack.get('synced_at')) if isinstance(pack, dict) else None
        generation = pack.get('generation') if isinstance(pack, dict) else None
        if (not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{64}', generation)
                or not synced or abs(now - synced) > DEPLOY_MAX_AGE):
            problems.append('NixKit pack sync stale or unavailable')
    if 't3_provider_auth' in data:
        auth = data['t3_provider_auth']
        if not isinstance(auth, dict):
            problems.append('T3 provider auth status unavailable')
        else:
            for provider in PROVIDERS:
                if auth.get(provider) is False:
                    problems.append(f'T3 provider {provider}: missing auth state')
    pool = data.get('reviewer_pool')
    cooldowns = data.get('reviewer_cooldowns')
    if isinstance(pool, list) and isinstance(cooldowns, dict):
        resets = {p: parse(cooldowns.get(p)) for p in PROVIDERS if p in pool}
        if not resets:
            problems.append('Reviewer pool has no configured providers')
        elif all(reset and reset > now for reset in resets.values()):
            timing = ', '.join(f'{p} until {reset.isoformat()}' for p, reset in resets.items())
            problems.append(f'Reviewer cooldowns exhausted the pool: {timing}')
    expiry = data.get('builder_pat_expires_on')
    if isinstance(expiry, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
        expires = parse(expiry)
        if expires and (expires.date() - now.date()).days <= PAT_NOTICE_DAYS:
            state = 'expired' if expires.date() < now.date() else 'expires'
            problems.append(f'Builder PAT {state} on {expires.date().isoformat()}')
    return problems


def cron_problems(profile, home):
    store = read_json(home / 'cron/jobs.json', missing=MISSING)
    if store is MISSING:
        return []
    jobs = store.get('jobs') if isinstance(store, dict) else store
    if not isinstance(jobs, list):
        return [f'{profile}: cron job store unreadable']
    problems = []
    for job in jobs:
        if not isinstance(job, dict) or job.get('enabled') is False:
            continue
        streak = job.get('failure_streak')
        if type(streak) is int and streak > 0:
            label = job.get('name')
            if not isinstance(label, str) or not SLUG.fullmatch(label):
                label = job.get('id')
            if not isinstance(label, str) or not SLUG.fullmatch(label):
                label = 'unnamed'
            problems.append(f'{profile}: cron {label} failing (streak {streak})')
    return problems


def usage_problems(homes, now):
    """Compare adjacent seven-day windows using only native, recorded usage."""
    middle = now - WEEK
    start = middle - WEEK
    totals = [[0, 0], [0, 0]]
    for home in homes:
        try:
            with (home / 'cron/usage_audit.jsonl').open('rb') as rows:
                for row in rows:
                    try:
                        entry = json.loads(row)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(entry, dict):
                        continue
                    when, tokens = parse(entry.get('ts')), entry.get('total_tokens')
                    if when and start <= when < now and type(tokens) is int and 0 <= tokens < 10**12:
                        totals[0][when >= middle] += tokens
        except OSError:
            pass
        path = home / 'state.db'
        if not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                # Rows contain cumulative spend. Exclude rows that cross a window boundary.
                for index, (low, high) in enumerate(((start, middle), (middle, now))):
                    cost, = db.execute('''
                        SELECT SUM(CASE WHEN actual_cost_usd > 0 THEN actual_cost_usd ELSE estimated_cost_usd END)
                          FROM session_model_usage
                         WHERE first_seen >= ? AND last_seen >= first_seen AND last_seen < ?
                    ''', (low.timestamp(), high.timestamp())).fetchone()
                    if cost is not None and 0 <= cost < 10**9:
                        totals[1][index] += cost
        except (OSError, sqlite3.Error):
            pass
    problems = []
    for (before, after), label, minimum in zip(totals, ('token', 'cost'), (100_000, 5)):
        if before > 0 and after >= 2 * before and after - before >= minimum:
            amounts = f'{after:,} vs {before:,}' if label == 'token' else f'${after:,.2f} vs ${before:,.2f}'
            problems.append(f'Weekly {label} spike: {amounts} in the previous week')
    return problems


def card_problems(board, owner, now, kanban):
    try:
        tasks = kanban(board)
        if not isinstance(tasks, list):
            raise ValueError('Expected a list of cards')
    except Exception:  # one unreadable board never hides the other findings
        return [f'{owner}: board {board} unreadable']
    problems = []
    cutoff = (now - BLOCKED_MAX_AGE).timestamp()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        card, status = task.get('id'), task.get('status')
        if not isinstance(card, str) or not SLUG.fullmatch(card):
            continue
        since = task.get('blocked_since') if status == 'blocked' else task.get('started_at')
        if type(since) not in (int, float) or not 0 < since < cutoff:
            continue
        if status == 'blocked':
            title = task.get('title', '')
            problems.append(f'{owner}: card {card} blocked >24h: {title[:80] if isinstance(title, str) else ""}')
        elif status in ('running', 'in_progress'):
            problems.append(f'{owner}: card {card} in_progress >24h')
    return problems


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
        problems.extend(snapshot_problems(data, now))
    used = disk if disk is not None else (lambda u: u.used / u.total)(shutil.disk_usage(home))
    if used > DISK_MAX_USED:
        problems.append(f'Host disk {used:.0%} used')
    if not (t3_ok if t3_ok is not None else t3_reachable()):
        problems.append('T3 Code is unreachable on 127.0.0.1:3773')
    kanban = kanban or board_tasks
    profiles = {'default': home}
    if (home / 'kanban.db').is_file():
        problems.extend(card_problems('default', 'default', now, kanban))
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
        problems.extend(card_problems(venture['board'], venture['id'], now, kanban))
        profile = venture.get('lead_profile')
        if profile is None:
            profile = venture['id']
        if isinstance(profile, str) and SLUG.fullmatch(profile) and profile != 'default':
            profiles[profile] = home / 'profiles' / profile
    for profile, directory in profiles.items():
        problems.extend(cron_problems(profile, directory))
    problems.extend(usage_problems(profiles.values(), now))
    return problems


def board_tasks(board):
    """Use the latest block or claim event so retries start a new clock."""
    def hermes(*argv):
        result = subprocess.run(['hermes', 'kanban', '--board', board, *argv, '--json'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(board)
        return json.loads(result.stdout or '[]')
    tasks = [task for task in hermes('list') if task['status'] in ('blocked', 'running', 'in_progress')]
    for task in tasks:
        details = hermes('show', task['id'])
        if details['task']['status'] != task['status']:
            task['status'] = None  # transitioned between list and show
            continue
        blocked = task['status'] == 'blocked'
        kind = 'blocked' if blocked else 'claimed'
        times = [event['created_at'] for event in details['events'] if event['kind'] == kind]
        if blocked or times:
            task['blocked_since' if blocked else 'started_at'] = max(times, default=None)
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
