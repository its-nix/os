#!/usr/bin/env python3
"""Root-side bridge from the Hermes spool to the founder's Nix Todoist project."""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import unicodedata
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
import uuid

ROOT = Path('/srv/nix-clean')
API = 'https://api.todoist.com/api/v1/'
# A private personal project: the shared `Nix` workspace project is visible to other members, and
# approval requests carry exact previews (outbound text, costs, lead details).
PROJECT_NAME = 'Nix approvals'
LABEL = 'nix-approval'
NAMESPACE = uuid.UUID('6f1c8e2a-6f3b-4d1e-9a52-2d6c3b1f0a77')
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{2,63}')
KEY = re.compile(r'[0-9a-f]{32}')
TODOIST_ID = re.compile(r'[A-Za-z0-9]{1,64}')
TEXT_LIMITS = {'target': 240, 'effect': 300, 'cost': 120, 'executor': 80, 'preview': 3000}
FIELDS = set(TEXT_LIMITS) | {'venture', 'action_id', 'expires', 'status_key'}
MAX_REQUEST_BYTES = 24576
MAX_ADMISSIONS = 20
MAX_POLLS = 5
POLL_DELAY = 60


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON key')
    return result


def validate(row, key, today):
    if not isinstance(row, dict) or set(row) != FIELDS or any(type(v) is not str for v in row.values()):
        raise ValueError('Unexpected request fields or types')
    if row['status_key'] != key or not all(SLUG.fullmatch(row[k]) for k in ('venture', 'action_id')):
        raise ValueError('Invalid request identity')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['expires']):
        raise ValueError('Expiry must be a UTC calendar date')
    if date.fromisoformat(row['expires']) > today + timedelta(days=30):
        raise ValueError('Expiry exceeds 30 days')
    for field, limit in TEXT_LIMITS.items():
        value = row[field]
        if not value.strip() or len(value) > limit or '`' in value:
            raise ValueError('Empty, oversized or unsafe request text')
        for char in value:
            if field == 'preview' and char in '\n\t':
                continue
            if unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'}:
                raise ValueError('Unsafe control or line separator')
    return row


def read_regular(path, limit, *, secret=False):
    # lstat before open also avoids touching an agent-created special file.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Expected a regular file')
    if not secret and hasattr(os, 'O_PATH'):  # Linux: pin the inode before opening untrusted data.
        inode = os.open(path, os.O_PATH | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(inode).st_mode):
                raise ValueError('Expected a regular file')
            fd = os.open(f'/proc/self/fd/{inode}', os.O_RDONLY)
        finally:
            os.close(inode)
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > limit:
            raise ValueError('Expected a bounded, non-linked regular file')
        if secret and (meta.st_uid != 0 or meta.st_gid != 0 or stat.S_IMODE(meta.st_mode) != 0o600):
            raise ValueError('Expected root:root mode 0600')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('File exceeds its size limit')
    return data


def atomic_status(directory, key, data):
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.write('\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, directory / (key + '.json'))
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def task_fields(row):
    # Keep title punctuation inert, and put the verbatim fields in a code block with no
    # agent-controlled closing fence. The trusted warning stays outside that block.
    summary = ''.join(c if c.isalnum() or c in ' -' else ' ' for c in row['effect'])
    summary = ' '.join(summary.split())[:120] or 'Review action'
    return {
        'content': f'[approval] {row["venture"]}: {summary}',
        'description': '\n'.join([
            f'Action id: {row["action_id"]}',
            'Completing this task does not authorize anything. Execute it yourself or reply with an explicitly bounded method.',
            '```', f'Venture: {row["venture"]}', f'Target: {row["target"]}',
            f'Exact effect: {row["effect"]}', f'Estimated cost: {row["cost"]}',
            f'Expires (end of UTC day): {row["expires"]}', f'Executor: {row["executor"]}',
            '', 'Preview:', row['preview'], '```',
        ]),
        'labels': [LABEL],
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Todoist:
    def __init__(self, token, project):
        self.token = token
        self.project = project
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, path, params=None, data=None, *, post=False):
        url = API + path + ('?' + urlencode(params) if params else '')
        request = Request(url, data=urlencode(data).encode() if data else None,
                          method='POST' if post else 'GET',
                          headers={'Authorization': 'Bearer ' + self.token})
        with self.opener.open(request, timeout=8) as reply:
            body = reply.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError('Todoist response exceeded its size limit')
            return json.loads(body) if body else None

    def check(self):
        project = self.call('projects/' + self.project)
        if (project['id'] != self.project or project['name'] != PROJECT_NAME
                or project['is_archived'] or project['is_shared'] or project.get('workspace_id') is not None):
            raise ValueError('Expected the active, private ' + PROJECT_NAME + ' project')

    def tasks(self, *, completed=False, now=None):
        params = {'project_id': self.project, 'limit': 100}
        endpoint, field = 'tasks', 'results'
        if completed:
            endpoint, field = 'tasks/completed/by_completion_date', 'items'
            params.update(since=(now - timedelta(days=89)).isoformat(),
                          until=(now + timedelta(seconds=1)).isoformat())
        for _ in range(20):
            page = self.call(endpoint, params)
            yield from (task for task in page[field] if task['project_id'] == self.project)
            cursor = page.get('next_cursor')  # absent on the last page of completed tasks
            if not cursor:
                return
            params['cursor'] = cursor
        raise ValueError('Todoist lookup exceeded its pagination limit')

    def add(self, row, fields):
        # Use the same UUID even after a lost response or local state restore.
        command_id = str(uuid.uuid5(NAMESPACE, row['action_id']))
        command = {'type': 'item_add', 'uuid': command_id, 'temp_id': command_id,
                   'args': dict(fields, project_id=self.project)}
        reply = self.call('sync', data={'commands': json.dumps([command]),
                                        'resource_types': '[]', 'sync_token': '*'}, post=True)
        if reply['sync_status'].get(command_id) != 'ok':
            raise ValueError('Todoist did not confirm the task')
        task_id = reply.get('temp_id_mapping', {}).get(command_id)
        if not isinstance(task_id, str) or not TODOIST_ID.fullmatch(task_id):
            raise ValueError('Todoist did not return an ID; reconcile on the next poll')
        return task_id

    def close(self, task_id):
        if not TODOIST_ID.fullmatch(task_id):
            raise ValueError('Invalid Todoist task ID')
        self.call('tasks/' + task_id + '/close', post=True)


def publish(directory, row):
    payload = {k: row[k] for k in ('action_id', 'venture', 'status', 'reason') if row[k] is not None}
    if row['task_id']:
        payload['todoist_task'] = row['task_id']
    atomic_status(directory, row['status_key'], payload)


def admit(db, outbox, directory, today):
    with os.scandir(outbox) as entries:
        for entry in entries:
            key = entry.name.removesuffix('.json')
            if not entry.name.endswith('.json') or not KEY.fullmatch(key):
                continue
            path = Path(entry.path)
            try:
                body = json.loads(read_regular(path, MAX_REQUEST_BYTES), object_pairs_hook=unique_object)
                request = validate(body, key, today)
            except FileNotFoundError:
                continue
            except (ValueError, OSError, RecursionError):
                if db.execute('SELECT 1 FROM actions WHERE status_key=?', (key,)).fetchone() is None:
                    atomic_status(directory, key, {'status': 'closed', 'reason': 'invalid-request'})
            else:
                with db:
                    inserted = db.execute(
                        'INSERT OR IGNORE INTO actions(action_id, venture, status_key, request) VALUES (?, ?, ?, ?)',
                        (request['action_id'], request['venture'], key, json.dumps(request))).rowcount
                if not inserted and db.execute('SELECT 1 FROM actions WHERE status_key=?', (key,)).fetchone() is None:
                    atomic_status(directory, key, {'action_id': request['action_id'], 'venture': request['venture'],
                                                   'status': 'closed', 'reason': 'action-id-already-used'})
            try:
                path.unlink(missing_ok=True)
            except IsADirectoryError:
                # Never descend into an agent-supplied directory, and never let one use an admission slot.
                try:
                    path.rmdir()
                except OSError:
                    pass
                continue
            yield


def matching(tasks, action_id, task_id):
    marker = 'Action id: ' + action_id + '\n'
    return [task for task in tasks if ((task.get('description') or '') + '\n').startswith(marker)
            and (task_id is None or task['id'] == task_id)]


def reconcile(db, state, remote, active, completed, now):
    row = json.loads(state['request'])
    fields = task_fields(row)
    task_id = state['task_id']
    expired = row['expires'] < now.date().isoformat()
    found = matching(active, row['action_id'], task_id)
    finished = [] if found else matching(completed(), row['action_id'], task_id)
    observed = found or finished
    if observed:
        if len(observed) != 1 or (not task_id and any(observed[0].get(k) != fields[k] for k in fields)):
            return 'closed', None, 'task-conflict'
        task = observed[0]
        if LABEL not in task['labels'] or not TODOIST_ID.fullmatch(task['id']):
            return 'closed', task_id, 'task-changed'
        task_id = task['id']
        if finished:
            return ('expired' if state['closing'] else 'completed-by-founder'), task_id, None
        if expired:
            if ((task.get('due') or {}).get('is_recurring') or task.get('parent_id')
                    or task['description'] != fields['description']
                    or any(child.get('parent_id') == task_id for child in active)):
                return 'closed', task_id, 'task-changed'
            # Record closing before the network call, so an ambiguous close remains an expiry.
            with db:
                db.execute('UPDATE actions SET task_id=?, closing=1 WHERE action_id=?', (task_id, row['action_id']))
            remote.close(task_id)
            return 'expired', task_id, None
        return 'pending', task_id, None
    if task_id:
        return 'closed', task_id, 'task-unavailable'
    if expired:
        return 'expired', None, None
    return 'pending', remote.add(row, fields), None


def tick(root, remote, now):
    spool = root / 'approvals'
    directory = spool / 'status'
    stamp = int(now.timestamp())
    with closing(sqlite3.connect(spool / 'state/queue.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY, venture TEXT NOT NULL, status_key TEXT UNIQUE NOT NULL,
            request TEXT NOT NULL, task_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT, closing INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 1,
            checked INTEGER NOT NULL DEFAULT 0
        )""")
        for _ in islice(admit(db, spool / 'outbox', directory, now.date()), MAX_ADMISSIONS):
            pass
        for row in db.execute('SELECT * FROM actions WHERE dirty=1').fetchall():
            publish(directory, row)
            with db:
                db.execute('UPDATE actions SET dirty=0 WHERE action_id=?', (row['action_id'],))
        rows = db.execute("SELECT * FROM actions WHERE status='pending' AND checked<=? ORDER BY checked, rowid LIMIT ?",
                          (stamp - POLL_DELAY, MAX_POLLS)).fetchall()
        if not rows:
            return
        remote.check()
        active = list(remote.tasks())
        history = None

        def completed():
            nonlocal history
            if history is None:
                history = list(remote.tasks(completed=True, now=now))
            return history

        for row in rows:
            # Rotate even after a request-specific failure, while global outages stop the run.
            with db:
                db.execute('UPDATE actions SET checked=? WHERE action_id=?', (stamp, row['action_id']))
            status, task_id, reason = reconcile(db, row, remote, active, completed, now)
            with db:
                db.execute('UPDATE actions SET status=?, task_id=?, reason=?, dirty=1 WHERE action_id=?',
                           (status, task_id, reason, row['action_id']))
            publish(directory, dict(row, status=status, task_id=task_id, reason=reason))
            with db:
                db.execute('UPDATE actions SET dirty=0 WHERE action_id=?', (row['action_id'],))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate root configuration and project access')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = ROOT / 'todoist'
        meta = config.lstat()
        if not stat.S_ISDIR(meta.st_mode) or (meta.st_uid, meta.st_gid, stat.S_IMODE(meta.st_mode)) != (0, 0, 0o700):
            raise ValueError('Invalid Todoist configuration directory')
        token = read_regular(config / 'token', 256, secret=True).decode('ascii').removesuffix('\n')
        project = read_regular(config / 'project-id', 65, secret=True).decode('ascii').removesuffix('\n')
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', token) or not TODOIST_ID.fullmatch(project):
            raise ValueError('Invalid token or project id format')
        remote = Todoist(token, project)
        if args.check:
            remote.check()
            print('Todoist credential and ' + PROJECT_NAME + ' project access verified.')
        else:
            tick(ROOT, remote, datetime.now(timezone.utc))
    except HTTPError as error:
        print(f'Todoist queue deferred (HTTP {error.code}).', file=sys.stderr)
        return 1
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError, sqlite3.Error):
        print('Todoist queue deferred. Check root configuration, project access and queue state.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
