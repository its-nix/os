#!/usr/bin/env python3
"""Queue a founder-gated external action. This tool NEVER performs the action.

  approval.py request --venture ID --action-id ID --target T --effect TEXT --preview FILE
                      [--cost TEXT] [--expires YYYY-MM-DD] [--executor founder]
  approval.py status  --venture ID --action-id ID
  approval.py record  --venture ID --action-id ID --outcome executed|declined|expired --evidence TEXT

Each action has one receipt file: $HERMES_HOME/ventures/<id>/actions/<action-id>.json. A request
is idempotent by action id. The local receipt stays `queued-local` until the host queue
reports delivery. Todoist completion is queue status only. `record` still requires evidence
of the founder's independently authenticated decision or execution.
"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{2,63}')
MAX_PREVIEW = 3000
OUTBOX = Path('/run/nix-approvals-outbox')
INBOX = Path('/run/nix-approvals-status')
STATUS_KEY = re.compile(r'[0-9a-f]{32}')
REQUEST_FIELDS = ('action_id', 'venture', 'target', 'effect', 'preview', 'cost', 'expires', 'executor', 'status_key')


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


@contextmanager
def admission_lock(venture):
    directory = venture_dir(venture)
    # Share the factory's lock so request admission cannot race a lifecycle transition.
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
        except FileNotFoundError:
            manifest = {}  # Existing non-registry approval callers.
        if manifest.get('lifecycle') in {'paused', 'retired'}:
            raise SystemExit('Venture is not accepting external approval requests')
        yield


def read_preview(venture, name):
    drafts = venture_dir(venture) / 'drafts'
    if '/' in name or name in ('', '.', '..'):
        raise SystemExit('Preview must be a file name directly inside the venture drafts/ folder')
    try:
        fd = os.open(drafts / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SystemExit('Preview must be a regular, non-symlink file inside the venture drafts/ folder') from None
    with os.fdopen(fd, encoding='utf-8', newline='') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise SystemExit('Preview must be a regular file')
        text = handle.read(MAX_PREVIEW + 1)
    if not text.strip():
        raise SystemExit('An exact preview of the external effect is required')
    if len(text) > MAX_PREVIEW:
        raise SystemExit(f'Preview exceeds {MAX_PREVIEW} characters; shorten it rather than truncating what the founder sees')
    return text


def write(path, data, *, receipt=False):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if receipt:
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def refresh(path, receipt):
    key = receipt.get('status_key', '')
    if receipt.get('outcome') or not STATUS_KEY.fullmatch(key):
        return receipt
    try:
        status = json.loads((INBOX / f'{key}.json').read_text())
    except FileNotFoundError:
        return receipt
    queue_status = status.get('status')
    if status.get('reason') != 'invalid-request' and (
            status.get('action_id'), status.get('venture')) != (receipt['action_id'], receipt['venture']):
        raise SystemExit('Queue status does not match the receipt')
    if queue_status not in ('pending', 'completed-by-founder', 'expired', 'closed'):
        raise SystemExit('Unrecognized approval queue status')
    task_id = status.get('todoist_task')
    if queue_status in ('expired', 'closed'):
        receipt['status'] = queue_status
    elif task_id:
        receipt['status'] = 'awaiting-founder'
    if task_id:
        receipt['todoist_task'] = task_id
    receipt['queue_status'] = queue_status
    if 'reason' in status:
        receipt['queue_reason'] = status['reason']
    write(path, receipt)
    return receipt


def request(args):
    path = receipt_path(args.venture, args.action_id)
    with admission_lock(args.venture), action_lock(path):
        if path.exists():
            receipt = refresh(path, json.loads(path.read_text()))
            if receipt['status'] != 'queued-local' or receipt.get('outcome') or receipt.get('queue_status'):
                return receipt
        else:
            receipt = {'action_id': args.action_id, 'venture': args.venture, 'target': args.target,
                       'effect': args.effect, 'preview': read_preview(args.venture, args.preview),
                       'cost': args.cost or 'none stated', 'expires': args.expires or 'none stated',
                       'executor': args.executor, 'requested_at': now(), 'status': 'queued-local', 'outcome': None}
        if 'status_key' not in receipt:
            if receipt['expires'] == 'none stated':
                receipt['expires'] = (date.fromisoformat(receipt['requested_at'][:10]) + timedelta(days=7)).isoformat()
            receipt['status_key'] = uuid.uuid4().hex
            write(path, receipt, receipt=True)
        try:
            write(OUTBOX / (receipt['status_key'] + '.json'), {k: receipt[k] for k in REQUEST_FIELDS})
        except FileNotFoundError:
            pass  # An undeployed spool leaves a durable local blocker.
        return receipt


def status(args):
    path = receipt_path(args.venture, args.action_id)
    if not path.exists():
        return {'status': 'unknown'}
    with action_lock(path):
        return refresh(path, json.loads(path.read_text()))


def record(args):
    path = receipt_path(args.venture, args.action_id)
    with action_lock(path):
        if not path.exists():
            raise SystemExit('Unknown action id')
        if not (args.evidence or '').strip():
            raise SystemExit('Evidence is required (execution receipt link, founder decision link)')
        receipt = refresh(path, json.loads(path.read_text()))
        if receipt.get('outcome'):
            raise SystemExit('Outcome already recorded; actions are never re-run')
        # Delivery (a Todoist task id) is what matters: a later queue status of closed/expired (task
        # moved, deleted, or past expiry) never blocks recording what actually happened.
        if args.outcome != 'expired' and not receipt.get('todoist_task'):
            raise SystemExit('Only an action the founder actually received can be executed or declined; '
                             'an undelivered request can only expire')
        receipt.update(outcome=args.outcome, evidence=args.evidence, recorded_at=now(), status='closed')
        write(path, receipt, receipt=True)
        return receipt


SAFE_FIELDS = ('action_id', 'venture', 'status', 'queue_status', 'queue_reason', 'outcome',
               'todoist_task', 'requested_at', 'recorded_at')


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
        result = status(args)
    else:
        result = record(args)
    print(json.dumps({k: result.get(k) for k in SAFE_FIELDS if k in result}, indent=2))  # no recipient/content PII
    return 0


if __name__ == '__main__':
    sys.exit(main())
