#!/usr/bin/env python3
"""Generate the daily founder scorecard from local receipts and read-only summaries."""

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import subprocess
import sys
import tempfile

from health import (APPROVAL_STATES, BACKUP_MAX_AGE, DEPLOY_MAX_AGE, LIFECYCLES, LOCAL_APPROVAL,
                    SLUG, parse, read_state, valid_health)

DAY = timedelta(days=1)
STATE = 'founder-digest.json'
QUEUE = Path('/run/nix-approvals-status')
ECONOMICS = {'revenue': 'Revenue', 'paying_customers': 'Paying customers', 'qualified_leads': 'Qualified leads'}
OPERATIONS = {
    'new_leads': 'New leads', 'blocked': 'Blocked cards', 'pending': 'Pending decisions',
    'requested': 'Requested approvals', 'executed': 'Executed approvals', 'declined': 'Declined approvals',
    'oldest_hours': 'Oldest pending (h)', 'incidents': 'Venture incidents', 'stalled': 'Stalled cards',
    'done': 'Done cards', 'merged_prs': 'Merged PRs', 'received_to_draft': 'Lead received → draft ready (min)',
    'draft_to_sent': 'Draft ready → founder sent (min)',
}


def object_(value):
    return value if isinstance(value, dict) else {}


def slug(value):
    return isinstance(value, str) and SLUG.fullmatch(value) is not None


def number(value):
    return type(value) is int or isinstance(value, float) and math.isfinite(value)


def command_json(command, timeout):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        return None


def board_counts(board, now):
    empty = dict.fromkeys(('blocked', 'stalled', 'done'))
    if not slug(board):
        return empty
    tasks = command_json([os.environ.get('HERMES_BIN', 'hermes'), '-p', 'default', 'kanban',
                          '--board', board, 'list', '--json'], timeout=15)
    if not isinstance(tasks, list) or any(not isinstance(row, dict) or not isinstance(row.get('status'), str)
                                          for row in tasks):
        return empty
    active = [row.get('started_at') for row in tasks if row['status'] == 'in_progress']
    return {'blocked': sum(row['status'] == 'blocked' for row in tasks),
            'stalled': sum(start <= (now - DAY).timestamp() for start in active) if all(map(number, active)) else None,
            'done': sum(row['status'] == 'done' for row in tasks)}


def crm_summary(home, venture, since, now):
    script = Path(__file__).resolve().parents[2] / 'crm-lead-ops/scripts/crm.py'
    if not script.is_file():
        script = home / 'skills/nix-os/crm-lead-ops/scripts/crm.py'
    return object_(command_json([sys.executable, str(script), 'summary', '--venture', venture,
                                 '--since', since.isoformat(), '--until', now.isoformat()], timeout=60))


def in_period(at, since, now):
    return at is not None and since <= at < now


def merged_pr_count(repo, since, now):
    if not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo, re.ASCII):
        return None
    limit = 100
    rows = command_json(['gh', 'pr', 'list', '--repo', repo, '--state', 'merged',
                         '--search', f'merged:>={since.date()}', '--limit', str(limit),
                         '--json', 'mergedAt'], timeout=15)
    if not isinstance(rows, list) or len(rows) >= limit:
        return None
    dates = [parse(object_(row).get('mergedAt')) for row in rows]
    return sum(in_period(at, since, now) for at in dates) if all(dates) else None


def receipt_status(row, venture):
    key = row.get('status_key')
    if isinstance(key, str) and re.fullmatch(r'[0-9a-f]{32}', key):
        queue = read_state(QUEUE / f'{key}.json') or {}
        if (queue.get('venture'), queue.get('action_id')) == (venture, row['action_id']):
            return queue.get('status'), queue.get('todoist_task') or row.get('todoist_task')
    return row.get('queue_status'), row.get('todoist_task')


def approvals(directory, since, now):
    counts = dict.fromkeys(('pending', 'requested', 'executed', 'declined'), 0)
    pending, draft_times, send_times = [], [], []
    complete = True
    for path in sorted((directory / 'actions').glob('*.json')):
        row = read_state(path) or {}
        action = row.get('action_id')
        if not slug(action) or row.get('status') not in APPROVAL_STATES:
            complete = False
            continue
        requested = parse(row.get('requested_at'))
        outcome = row.get('outcome')
        recorded = parse(row.get('recorded_at'))
        complete &= requested is not None and (outcome not in ('executed', 'declined') or recorded is not None)
        counts['requested'] += in_period(requested, since, now)
        if outcome in ('executed', 'declined'):
            counts[outcome] += in_period(recorded, since, now)

        expires = parse(row.get('expires'))
        if not outcome and row.get('status') in (*LOCAL_APPROVAL, 'awaiting-founder') \
                and (not expires or expires.date() >= now.date()) and (not requested or requested <= now):
            queue, task = receipt_status(row, directory.name)
            if queue not in ('expired', 'closed'):
                age = (now - requested).total_seconds() / 3600 if requested else None
                step = ('check founder confirmation' if queue == 'completed-by-founder'
                        else 'founder to decide' if task or row['status'] == 'awaiting-founder'
                        else 'waiting for queue delivery')
                link = task if isinstance(task, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', task) else None
                pending.append((row.get('revenue_blocking') is True, age, directory.name, action, step, link))

        received = parse(row.get('lead_received_at'))
        ready = parse(row.get('draft_ready_at'))
        sent = parse(row.get('founder_sent_at'))
        if in_period(ready, since, now) and received and received <= ready:
            draft_times.append((ready - received).total_seconds() / 60)
        if outcome == 'executed' and in_period(sent, since, now) and ready and ready <= sent:
            send_times.append((sent - ready).total_seconds() / 60)

    counts['pending'] = len(pending)
    ages = [age for _, age, *_ in pending if age is not None]
    counts['oldest_hours'] = round(max(ages), 2) if ages else None
    if not complete:
        counts = dict.fromkeys(counts)
    counts.update(received_to_draft=round(median(draft_times), 2) if draft_times else None,
                  draft_to_sent=round(median(send_times), 2) if send_times else None)
    return counts, pending


def health_summary(home, now):
    status = read_state(home / 'nix/health.json')
    if not valid_health(status):
        return None, [], None
    backup = parse(status['backup_last_success'])
    deployed = parse(status['deployed_at'])
    flags = []
    if not backup or now - backup > BACKUP_MAX_AGE or status.get('backup_last_attempt_status') == 'failed':
        flags.append('Recovery backup stale, missing, or last attempt failed')
    if not deployed or now - deployed > DEPLOY_MAX_AGE:
        flags.append('Healthy deployment missing or stale')
    if status.get('deploy_attempt_status') == 'failed':
        flags.append('Latest deployment attempt failed')
    revision = status.get('deployed_revision')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision) or not deployed:
        revision = None
    return len(flags), flags, revision


def display_value(value):
    if not number(value):
        return 'unavailable'
    return f'{value:g}' if isinstance(value, float) else str(value)


def table(labels, current, previous):
    lines = ['```', '| Metric | Current | Previous | Delta |', '| --- | --- | --- | --- |']
    for key, label in labels.items():
        value, before = current.get(key), previous.get(key)
        delta = round(value - before, 2) if number(value) and number(before) else None
        change = ('+' if number(delta) and delta > 0 else '') + display_value(delta)
        lines.append(f'| {label} | {display_value(value)} | {display_value(before)} | {change} |')
    return [*lines, '```']


def build(home, now, previous):
    since = now - DAY
    lines = [f'# Founder scorecard · {now:%Y-%m-%d %H:%M} UTC',
             f'24 h activity window since {since:%Y-%m-%d %H:%M} UTC.']
    if stamp := parse(previous.get('at')):
        lines.append(f'Previous snapshot from {stamp:%Y-%m-%d %H:%M} UTC.')
    snapshots, decisions = {}, []
    for path in sorted((home / 'ventures').glob('*/manifest.json')):
        venture = path.parent.name
        if not slug(venture):
            continue
        manifest = read_state(path) or {}
        lifecycle = manifest.get('lifecycle')
        lines.extend(['', f'## {venture} · {lifecycle if lifecycle in LIFECYCLES else "unavailable"}'])
        declared = object_(manifest.get('outcome_metrics'))
        custom = {key: key.replace('_', ' ') for key in sorted(declared)
                  if slug(key) and key not in ECONOMICS and key not in OPERATIONS}
        values = {key: declared.get(key) for key in (*ECONOMICS, *custom, 'incidents')}
        values = {key: value if number(value) else None for key, value in values.items()}
        crm = {}
        integrations = object_(manifest.get('integrations'))
        if object_(integrations.get('crm')).get('enabled') is True:
            crm = crm_summary(home, venture, since, now)
            if values['qualified_leads'] is None:
                values['qualified_leads'] = crm.get('qualified_leads')
        elif manifest:
            crm = {'status': 'disabled'}
        values['new_leads'] = crm.get('new_leads')
        values['merged_prs'] = merged_pr_count(integrations.get('github_repo'), since, now)
        values.update(board_counts(manifest.get('board'), now))
        counts, pending = approvals(path.parent, since, now)
        values.update(counts)
        decisions.extend(pending)
        labels = {**ECONOMICS, **custom, **OPERATIONS}
        snapshots[venture] = values
        lines.extend(table(labels, values, object_(object_(previous.get('ventures')).get(venture))))
        if crm.get('status') == 'ok':
            stages = object_(crm.get('stages'))
            lines.append('CRM stages · ' + ', '.join(f'{stage} {display_value(stages.get(stage))}'
                         for stage in ('NEW', 'SCREENING', 'MEETING', 'PROPOSAL', 'CUSTOMER', 'LOST', 'OTHER')))
            timings = object_(crm.get('stage_timings'))
            samples = []
            for stage in ('SCREENING', 'MEETING', 'PROPOSAL', 'CUSTOMER'):
                stats = object_(timings.get(stage))
                if stats:
                    samples.append(f'{stage} {display_value(stats.get("median_minutes"))} min '
                                   f'({display_value(stats.get("samples"))})')
            lines.append('CRM qualification timings · ' + (', '.join(samples) or 'unavailable'))
        else:
            lines.append('CRM disabled' if crm.get('status') == 'disabled' else 'CRM unavailable')

    if not snapshots:
        lines.append('No venture manifests found.')
    if decisions:
        lines.extend(['', '## Pending decisions'])
        for revenue, age, venture, action, step, task in sorted(
                decisions, key=lambda row: (not row[0], -(row[1] if row[1] is not None else -1), row[2], row[3])):
            flag = ' · revenue blocker' if revenue else ''
            todoist = f' · Todoist task {task}' if task else ''
            age_label = f'{round(age, 2):g} h' if age is not None else 'age unavailable'
            lines.append(f'- {venture}/{action} · {age_label}{flag} · {step}{todoist}')
    incidents, flags, revision = health_summary(home, now)
    lines.extend(['', '## Company health', *table({'incidents': 'Health incidents'},
                  {'incidents': incidents}, object_(previous.get('health')))])
    lines.extend(f'- {flag}' for flag in flags)
    lines.append(f'Last verified deployed revision · {revision[:12] if revision else "unavailable"}')
    return '\n'.join(lines), {'at': now.isoformat(), 'ventures': snapshots, 'health': {'incidents': incidents}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Print the digest without advancing the snapshot')
    parser.add_argument('--at', help='Override the current UTC time for replay or fixtures (ISO 8601)')
    args = parser.parse_args(argv)
    now = parse(args.at) if args.at else datetime.now(timezone.utc)
    if now is None:
        parser.error('expected an ISO 8601 timestamp')
    now = now.astimezone(timezone.utc)
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    state = home / STATE
    if args.preview:
        print(build(home, now, read_state(state) or {})[0])
        return
    with state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_state(state) or {}
        output, snapshot = build(home, now, previous)
        if parse(previous.get('at')) is None or now > parse(previous['at']):
            with tempfile.NamedTemporaryFile(mode='w', dir=home, prefix='.founder-digest-', delete=False) as temp:
                temporary = Path(temp.name)
                try:
                    json.dump(snapshot, temp, separators=(',', ':'))
                    temp.flush()
                    os.replace(temporary, state)
                finally:
                    temporary.unlink(missing_ok=True)
    print(output)


if __name__ == '__main__':
    main()
