#!/usr/bin/env python3
"""Idempotent launch guard and outcome receipt for T3 coding threads (one Kanban card, one thread).

  launch_guard.py begin    --venture V --card C [--branch B] [--recover --reason TEXT]
  launch_guard.py launched --venture V --card C --thread-id T [--provider P] [--branch B] [--link URL]
  launch_guard.py complete --venture V --card C --pr URL [--ci S] [--review S] [--merge S] [--deploy S] [--evidence TEXT]
  launch_guard.py show     --venture V --card C
  launch_guard.py receipt  --venture V --card C

State is one record per card at $HERMES_HOME/ventures/<V>/t3/<C>.json (status intent -> launched ->
completed), written atomically under a per-card lock. `begin` runs BEFORE t3_thread_launch and
succeeds once: a second call (retry, lost reply, restart) is refused and says which thread to read.
A relaunch needs `--recover --reason` and is only for an operator who has read the old thread.

Exit codes: 0 ok, 2 invalid input or unreadable record, 3 refused (a launch already exists),
4 invalid state transition, 5 lock busy.
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

OK, INVALID, REFUSED, CONFLICT, BUSY = 0, 2, 3, 4, 5
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
PR = re.compile(r'^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$')
STATES = {
    'ci': ('pending', 'green', 'red', 'n/a'),
    'review': ('pending', 'approved', 'changes-requested', 'n/a'),
    'merge': ('open', 'auto-merge-armed', 'merged', 'closed', 'n/a'),
    'deploy': ('pending', 'deployed', 'failed', 'not-applicable'),
}


class Refusal(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def paths(args):
    for label in ('venture', 'card'):
        if not ID.match(getattr(args, label)):
            raise Refusal(INVALID, f'invalid {label} id (letters, digits, _ . - only)')
    base = Path(os.environ.get('HERMES_HOME', '/opt/data')) / 'ventures' / args.venture / 't3'
    return base / f'{args.card}.json', base / f'{args.card}.lock'


@contextmanager
def locked(lock_path, timeout):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a') as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Refusal(BUSY, 'another launch_guard call holds this card lock; retry shortly')
                time.sleep(0.05)
        yield


def load(path):
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or record.get('status') not in ('intent', 'launched', 'completed'):
            raise ValueError('unexpected shape')
        return record
    except ValueError as exc:
        raise Refusal(INVALID, f'record {path.name} is unreadable ({exc}); inspect it, do not relaunch blindly')


def store(path, record):
    record['updatedAt'] = now()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.t3-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def guidance(record):
    """What the lead does next with an existing record. Never relaunch from this."""
    thread = record.get('threadId')
    if record['status'] == 'intent':
        return {'action': 'search_thread',
                'next': f'No thread id is recorded. Call t3_thread_search for "{record["card"]}:". '
                        'If a thread exists, run `launched --thread-id` with it and read it. If none '
                        'exists after reading the search result, recover with `begin --recover --reason`.'}
    return {'action': 'read_thread', 'threadId': thread,
            'next': f'Read thread {thread} (t3_thread_read) and continue with t3_thread_send. '
                    'Do not call t3_thread_launch for this card.'}


def cmd_begin(args, record, path):
    if record is None:
        if args.recover:
            raise Refusal(CONFLICT, 'nothing to recover: this card has no launch record')
        branch = args.branch or f'{args.venture}/{args.card}'
        record = {'venture': args.venture, 'card': args.card, 'status': 'intent', 'attempt': 1,
                  'branch': branch, 'threadId': None, 'prUrl': None, 'createdAt': now(), 'history': []}
        store(path, record)
        return {'action': 'launch', 'status': 'intent', 'branch': branch,
                'next': 'Call t3_thread_launch now, then run `launched --thread-id <id>`.'}
    if not args.recover:
        raise Refusal(REFUSED, f'a launch already exists for this card (status {record["status"]})',
                      status=record['status'], branch=record.get('branch'), prUrl=record.get('prUrl'),
                      **guidance(record))
    if not (args.reason or '').strip():
        raise Refusal(INVALID, '--recover needs --reason naming what you read in the old thread')
    record['history'].append({k: record.get(k) for k in ('attempt', 'status', 'threadId', 'branch', 'prUrl')}
                             | {'recoveredAt': now(), 'reason': args.reason.strip()})
    record['attempt'] += 1
    record.update(status='intent', threadId=None, prUrl=None,
                  branch=args.branch or f'{args.venture}/{args.card}-r{record["attempt"]}')
    record.pop('launchedAt', None), record.pop('completedAt', None), record.pop('receipt', None)
    store(path, record)
    return {'action': 'launch', 'status': 'intent', 'branch': record['branch'], 'attempt': record['attempt'],
            'next': 'Recovery recorded. Call t3_thread_launch on the new branch, then run `launched`.'}


def cmd_launched(args, record, path):
    if record is None:
        raise Refusal(CONFLICT, 'run `begin` before launching')
    if record['status'] != 'intent':
        if record.get('threadId') == args.thread_id:
            return {'action': 'noop', 'status': record['status'], 'threadId': args.thread_id}
        raise Refusal(CONFLICT, f'card already has thread {record.get("threadId")}; refusing {args.thread_id}',
                      **guidance(record))
    record.update(status='launched', threadId=args.thread_id, launchedAt=now())
    for key, value in (('provider', args.provider), ('branch', args.branch), ('link', args.link)):
        if value:
            record[key] = value
    store(path, record)
    return {'action': 'noop', 'status': 'launched', 'threadId': args.thread_id}


def cmd_complete(args, record, path):
    if record is None or record['status'] == 'intent':
        raise Refusal(CONFLICT, 'complete needs a launched thread (run `launched` first)')
    if not PR.match(args.pr):
        raise Refusal(INVALID, '--pr must be a https://github.com/<owner>/<repo>/pull/<n> URL')
    if record.get('prUrl') not in (None, args.pr):
        raise Refusal(CONFLICT, f'card already records {record["prUrl"]}; a different PR needs --recover')
    receipt = record.setdefault('receipt', {})
    for key in (*STATES, 'evidence'):
        value = getattr(args, key)
        if value is not None:
            receipt[key] = value
    if record['status'] != 'completed':
        record.update(status='completed', completedAt=now())
    record['prUrl'] = args.pr
    store(path, record)
    return {'action': 'noop', 'status': 'completed', 'prUrl': args.pr, 'receipt': receipt}


def render_receipt(record):
    receipt = record.get('receipt', {})
    rows = [('Thread', f'{record.get("threadId") or "-"}' + (f' ({record["link"]})' if record.get('link') else '')),
            ('Provider', record.get('provider') or '-'), ('Branch', record.get('branch') or '-'),
            ('PR', record.get('prUrl') or '-'), ('CI', receipt.get('ci', 'pending')),
            ('Review', receipt.get('review', 'pending')), ('Merge', receipt.get('merge', 'open')),
            ('Deploy', receipt.get('deploy', 'pending')),
            ('Verification evidence', receipt.get('evidence') or '-')]
    return '\n'.join([f'**T3 outcome receipt** · card {record["card"]} · status {record["status"]} '
                      f'· attempt {record.get("attempt", 1)}'] + [f'- {name}: {value}' for name, value in rows])


def cmd_show(args, record, path):
    if record is None:
        raise Refusal(CONFLICT, 'no launch record for this card')
    return record


def cmd_receipt(args, record, path):
    if record is None:
        raise Refusal(CONFLICT, 'no launch record for this card')
    print(render_receipt(record))
    return None


def parser():
    top = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    top.add_argument('--lock-timeout', type=float, default=10.0, help=argparse.SUPPRESS)
    sub = top.add_subparsers(dest='command', required=True)
    for name in ('begin', 'launched', 'complete', 'show', 'receipt'):
        p = sub.add_parser(name)
        p.add_argument('--venture', required=True)
        p.add_argument('--card', required=True)
        if name == 'begin':
            p.add_argument('--branch')
            p.add_argument('--recover', action='store_true')
            p.add_argument('--reason')
        if name == 'launched':
            p.add_argument('--thread-id', required=True)
            p.add_argument('--provider')
            p.add_argument('--branch')
            p.add_argument('--link')
        if name == 'complete':
            p.add_argument('--pr', required=True)
            for key, choices in STATES.items():
                p.add_argument(f'--{key}', choices=choices)
            p.add_argument('--evidence', type=lambda v: v[:2000])
    return top


COMMANDS = {'begin': cmd_begin, 'launched': cmd_launched, 'complete': cmd_complete,
            'show': cmd_show, 'receipt': cmd_receipt}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        path, lock = paths(args)
        with locked(lock, args.lock_timeout):
            result = COMMANDS[args.command](args, load(path), path)
    except Refusal as refusal:
        print(json.dumps({'error': str(refusal), **refusal.extra}, indent=2, sort_keys=True), file=sys.stderr)
        return refusal.code
    if result is not None:
        print(json.dumps(result, indent=2, sort_keys=True))
    return OK


if __name__ == '__main__':
    sys.exit(main())
