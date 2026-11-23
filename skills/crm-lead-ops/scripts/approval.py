#!/usr/bin/env python3
"""Queue a founder-gated external action. This tool NEVER performs the action.

  approval.py request --venture ID --action-id ID --target T --effect TEXT --preview FILE
                      [--cost TEXT] [--expires YYYY-MM-DD] [--executor founder]
  approval.py status  --venture ID --action-id ID
  approval.py record  --venture ID --action-id ID --outcome executed|declined|expired --evidence TEXT

Each action has one receipt file: $HERMES_HOME/ventures/<id>/actions/<action-id>.json. A request
is idempotent by action id (re-queuing returns the existing receipt). With TODOIST_API_TOKEN set,
one Todoist task is created via the Sync API with a command UUID derived from the action id
(label nix-approval), so retries never duplicate it. Without it, the
request stays `queued-local` and is reported as blocked. A completed Todoist task is NOT approval:
only `record`, written after the founder executed (or explicitly approved a bounded method), changes
the outcome, and it requires evidence.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid

ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{2,63}')
TODOIST_SYNC = 'https://api.todoist.com/api/v1/sync'
MAX_PREVIEW = 3000
NAMESPACE = uuid.UUID('6f1c8e2a-6f3b-4d1e-9a52-2d6c3b1f0a77')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def home():
    path = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    return path.parent.parent if path.parent.name == 'profiles' else path


def venture_dir(venture):
    if not ID_RE.fullmatch(venture or ''):
        raise SystemExit('venture must be a lowercase slug')
    base = home() / 'ventures'
    path = base / venture
    for part in (base, path, path / 'actions', path / 'drafts'):
        if part.is_symlink():
            raise SystemExit('Refusing a symlinked venture path')
    return path


def receipt_path(venture, action_id):
    if not ID_RE.fullmatch(action_id or ''):
        raise SystemExit('action id must be a lowercase slug (3-64 chars)')
    return venture_dir(venture) / 'actions' / f'{action_id}.json'


@contextmanager
def action_lock(path):
    """Serialize every transition of one action (request, retry, record)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix('.lock'), 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_preview(venture, name):
    drafts = venture_dir(venture) / 'drafts'
    if '/' in name or name in ('', '.', '..'):
        raise SystemExit('Preview must be a file name directly inside the venture drafts/ folder')
    try:
        fd = os.open(drafts / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SystemExit('Preview must be a regular, non-symlink file inside the venture drafts/ folder') from None
    with os.fdopen(fd) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise SystemExit('Preview must be a regular file')
        text = handle.read(MAX_PREVIEW + 1)
    if not text.strip():
        raise SystemExit('An exact preview of the external effect is required')
    if len(text) > MAX_PREVIEW:
        raise SystemExit(f'Preview exceeds {MAX_PREVIEW} characters; shorten it rather than truncating what the founder sees')
    return text


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def todoist_task(receipt, token, post=None):
    """Create the founder task via the Sync API. The command UUID is derived from the action id,
    and Todoist ignores a repeated command UUID, so a retry after a timeout or crash never creates
    a second task."""
    command_uuid = str(uuid.uuid5(NAMESPACE, f'{receipt["venture"]}/{receipt["action_id"]}'))
    content = f'[approval] {receipt["venture"]}: {receipt["effect"][:120]}'
    description = '\n'.join([
        f'Action id: {receipt["action_id"]}', f'Venture: {receipt["venture"]}', f'Target: {receipt["target"]}',
        f'Exact effect: {receipt["effect"]}', f'Estimated cost: {receipt["cost"]}', f'Expires: {receipt["expires"]}',
        f'Executor: {receipt["executor"]}', '', 'Preview:', receipt['preview'], '',
        'Completing this task does not authorize anything. Execute it yourself or reply with an explicitly bounded method.'])
    command = {'type': 'item_add', 'uuid': command_uuid, 'temp_id': command_uuid,
               'args': {'content': content, 'description': description, 'labels': ['nix-approval'], 'priority': 3}}
    if post:
        return post(command)
    data = urllib.parse.urlencode({'commands': json.dumps([command])}).encode()
    request = urllib.request.Request(TODOIST_SYNC, data=data, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=30) as response:
        reply = json.load(response)
    if reply.get('sync_status', {}).get(command_uuid) != 'ok':
        raise SystemExit('Todoist did not confirm the approval task; the request stays pending and is safe to retry')
    task_id = reply.get('temp_id_mapping', {}).get(command_uuid) or find_task(receipt['action_id'], token)
    if not task_id:
        raise SystemExit('Todoist accepted the command but the task cannot be found; reconcile manually before retrying')
    return task_id


def find_task(action_id, token):
    """A replayed command returns no id mapping: locate the original task by its action id."""
    request = urllib.request.Request('https://api.todoist.com/api/v1/tasks?label=nix-approval&limit=200',
                                     headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.load(response).get('results', [])
    return next((t['id'] for t in results if f'Action id: {action_id}\n' in (t.get('description') or '') + '\n'), None)


def request(args, post=None):
    path = receipt_path(args.venture, args.action_id)
    with action_lock(path):
        if path.exists():
            receipt = json.loads(path.read_text())
            if receipt['status'] not in ('queued-local', 'submitting') or receipt.get('outcome'):
                return receipt  # idempotent: never queue the same action twice
        else:
            receipt = {'action_id': args.action_id, 'venture': args.venture, 'target': args.target,
                       'effect': args.effect, 'preview': read_preview(args.venture, args.preview),
                       'cost': args.cost or 'none stated', 'expires': args.expires or 'none stated',
                       'executor': args.executor, 'requested_at': now(), 'status': 'queued-local', 'outcome': None}
            write(path, receipt)  # durable before any external call
        token = os.environ.get('TODOIST_API_TOKEN')
        if token or post:
            receipt['status'] = 'submitting'  # an uncertain submission is retried with the same command UUID
            write(path, receipt)
            receipt['todoist_task'] = todoist_task(receipt, token, post)
            receipt['status'] = 'awaiting-founder'
            write(path, receipt)
        return receipt


def record(args):
    path = receipt_path(args.venture, args.action_id)
    with action_lock(path):
        if not path.exists():
            raise SystemExit('Unknown action id')
        if not (args.evidence or '').strip():
            raise SystemExit('Evidence is required (execution receipt link, founder decision link)')
        receipt = json.loads(path.read_text())
        if receipt.get('outcome'):
            raise SystemExit('Outcome already recorded; actions are never re-run')
        if args.outcome != 'expired' and receipt['status'] != 'awaiting-founder':
            raise SystemExit('Only an action the founder actually received can be executed or declined; '
                             'an undelivered request can only expire')
        receipt.update(outcome=args.outcome, evidence=args.evidence, recorded_at=now(), status='closed')
        write(path, receipt)
        return receipt


SAFE_FIELDS = ('action_id', 'venture', 'status', 'outcome', 'todoist_task', 'requested_at', 'recorded_at')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('request')
    for flag in ('--venture', '--action-id', '--target', '--effect'):
        r.add_argument(flag, required=True)
    r.add_argument('--preview', required=True, help='file name inside $HERMES_HOME/ventures/<id>/drafts/')
    r.add_argument('--cost')
    r.add_argument('--expires')
    r.add_argument('--executor', default='founder')
    s = sub.add_parser('status')
    s.add_argument('--venture', required=True)
    s.add_argument('--action-id', required=True)
    c = sub.add_parser('record')
    c.add_argument('--venture', required=True)
    c.add_argument('--action-id', required=True)
    c.add_argument('--outcome', required=True, choices=['executed', 'declined', 'expired'])
    c.add_argument('--evidence', required=True)
    args = parser.parse_args(argv)
    if args.command == 'request':
        result = request(args)
    elif args.command == 'status':
        path = receipt_path(args.venture, args.action_id)
        result = json.loads(path.read_text()) if path.exists() else {'status': 'unknown'}
    else:
        result = record(args)
    print(json.dumps({k: result.get(k) for k in SAFE_FIELDS if k in result}, indent=2))  # no recipient/content PII
    return 0


if __name__ == '__main__':
    sys.exit(main())
