#!/usr/bin/env python3
"""Queue a founder-gated external action. This tool NEVER performs the action.

  approval.py request --venture ID --action-id ID --target T --effect TEXT --preview FILE
                      [--cost TEXT] [--expires YYYY-MM-DD] [--executor founder]
  approval.py status  --venture ID --action-id ID
  approval.py record  --venture ID --action-id ID --outcome executed|declined|expired --evidence TEXT

Each action has one receipt file: $HERMES_HOME/ventures/<id>/actions/<action-id>.json. A request
is idempotent by action id (re-queuing returns the existing receipt). With TODOIST_API_TOKEN set,
one Todoist task is created in the founder's inbox project (label nix-approval). Without it, the
request stays `queued-local` and is reported as blocked. A completed Todoist task is NOT approval:
only `record`, written after the founder executed (or explicitly approved a bounded method), changes
the outcome, and it requires evidence.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.request

ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{2,63}')
TODOIST = 'https://api.todoist.com/api/v1/tasks'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def home():
    path = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    return path.parent.parent if path.parent.name == 'profiles' else path


def receipt_path(venture, action_id):
    if not ID_RE.fullmatch(venture or '') or not ID_RE.fullmatch(action_id or ''):
        raise SystemExit('venture and action id must be lowercase slugs (3-64 chars)')
    return home() / 'ventures' / venture / 'actions' / f'{action_id}.json'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def todoist_task(receipt, token, post=None):
    content = f'[approval] {receipt["venture"]}: {receipt["effect"][:120]}'
    description = '\n'.join([
        f'Action id: {receipt["action_id"]}', f'Venture: {receipt["venture"]}', f'Target: {receipt["target"]}',
        f'Exact effect: {receipt["effect"]}', f'Estimated cost: {receipt["cost"]}', f'Expires: {receipt["expires"]}',
        f'Executor: {receipt["executor"]}', '', 'Preview:', receipt['preview'][:3000], '',
        'Completing this task does not authorize anything. Execute it yourself or reply with an explicitly bounded method.'])
    body = {'content': content, 'description': description, 'labels': ['nix-approval'], 'priority': 3}
    if post:
        return post(body)
    request = urllib.request.Request(TODOIST, data=json.dumps(body).encode(), method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
        'X-Request-Id': receipt['action_id']})  # Todoist de-duplicates retried requests by id
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get('id')


def request(args, post=None):
    path = receipt_path(args.venture, args.action_id)
    if path.exists():
        existing = json.loads(path.read_text())
        if existing['status'] != 'queued-local' or not os.environ.get('TODOIST_API_TOKEN'):
            return existing  # idempotent: never queue the same action twice
        receipt = existing
    else:
        preview = Path(args.preview).read_text() if args.preview else ''
        if not preview.strip():
            raise SystemExit('An exact preview of the external effect is required')
        receipt = {'action_id': args.action_id, 'venture': args.venture, 'target': args.target,
                   'effect': args.effect, 'preview': preview, 'cost': args.cost or 'none stated',
                   'expires': args.expires or 'none stated', 'executor': args.executor,
                   'requested_at': now(), 'status': 'queued-local', 'outcome': None}
        write(path, receipt)  # durable before any external call
    token = os.environ.get('TODOIST_API_TOKEN')
    if token or post:
        receipt['todoist_task'] = todoist_task(receipt, token, post)
        receipt['status'] = 'awaiting-founder'
        write(path, receipt)
    return receipt


def record(args):
    path = receipt_path(args.venture, args.action_id)
    if not path.exists():
        raise SystemExit('Unknown action id')
    if not (args.evidence or '').strip():
        raise SystemExit('Evidence is required (execution receipt link, founder decision link)')
    receipt = json.loads(path.read_text())
    if receipt.get('outcome'):
        raise SystemExit('Outcome already recorded; actions are never re-run')
    receipt.update(outcome=args.outcome, evidence=args.evidence, recorded_at=now(), status='closed')
    write(path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('request')
    for flag in ('--venture', '--action-id', '--target', '--effect'):
        r.add_argument(flag, required=True)
    r.add_argument('--preview', required=True)
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
    print(json.dumps({k: v for k, v in result.items() if k != 'preview'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
