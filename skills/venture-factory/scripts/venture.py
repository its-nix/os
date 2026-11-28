#!/usr/bin/env python3
"""Generic, idempotent venture lifecycle for one multi-profile Hermes runtime.

Runs inside the Hermes container (HERMES_HOME). The registry is native runtime state at
$HERMES_HOME/ventures/<id>/; Git holds only this tool and its templates. Every native
cron mutation goes through the `hermes` CLI. Board admission uses the pinned Kanban store.

  venture.py create  --id ID --name NAME --mission TEXT --approval-ref REF [--prd F] [--gtm F]
                     [--pack P ...] [--crm] [--repo OWNER/NAME] [--timezone TZ] [--dry-run]
  venture.py pause|resume|retire --id ID [--reason TEXT]
  venture.py stage   --id ID --to operating|scaling|pivoting
  venture.py reconcile --id ID
  venture.py release --id ID TASK_ID [TASK_ID ...]
  venture.py status  [--id ID]

No venture name is built in. A pre-existing profile that this registry did not create is
never adopted, so historical/legacy profiles cannot be reactivated by a new venture.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import string
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / 'templates'
SCHEMA_VERSION = 1
ID_RE = re.compile(r'[a-z][a-z0-9]{1,23}')
# Names that already mean something in Hermes or in the company runtime.
RESERVED = {'default', 'lnx', 'personal', 'engineer', 'reviewer', 'root', 'admin', 'hermes', 'nix'}
LIFECYCLE = ('approved', 'incubating', 'operating', 'scaling', 'pivoting', 'paused', 'retired')
ACTIVE = {'incubating', 'operating', 'scaling', 'pivoting'}
CORE_PACKS = ['core']
DEFAULT_PACKS = ['core', 'product', 'research', 'growth', 'sales']
PACKS = {'core', 'engineering', 'product', 'growth', 'sales', 'content', 'design', 'research',
         'operations', 'data-ai'}
MARKER = 'nix-venture.json'
BUDGETS = {
    'max_cards_per_window': 3, 'max_attempt_minutes': 5, 'failure_limit': 2,
    'max_work_minutes_per_window': 30, 'max_concurrent_workers': 1, 'spend_limit': 0,
}
FACTORY = '/opt/data/skills/nix-os/venture-factory/scripts/venture.py'
CONSTRAINT = re.compile(r'^#{1,6} Current constraint\s*$', re.MULTILINE)
RETIRED_KEYS = re.compile(r'^\s*(?:export\s+)?(?:MCP_T3_API_KEY|TWENTY_API_KEY)\s*=')


class Refusal(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Hermes:
    """Thin CLI wrapper; tests substitute HERMES_BIN with a fake."""

    def __init__(self, home, binary=None, dry_run=False):
        self.home = Path(home).expanduser().absolute()
        self.binary = binary or os.environ.get('HERMES_BIN', 'hermes')
        self.dry_run = dry_run
        self.planned = []

    def run(self, *argv, profile=None, board=None, mutate=True):
        cmd = [self.binary]
        if profile:
            cmd += ['-p', profile]
        if board:
            cmd += ['kanban', '--board', board]
        cmd += list(argv)
        if mutate and self.dry_run:
            self.planned.append(cmd)
            return ''
        env = dict(os.environ, HERMES_HOME=str(self.home))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
        except subprocess.TimeoutExpired:
            raise Refusal(f'hermes command timed out: {" ".join(cmd[1:4])} …') from None
        if result.returncode:
            raise Refusal(f'hermes command failed: {" ".join(cmd[1:4])} …')
        return result.stdout

    def profile_dir(self, name):
        return self.home / 'profiles' / name

    def boards(self):
        if self.dry_run:
            # Read-only: board metadata files only (the CLI listing may initialize databases).
            found = {}
            for meta in sorted((self.home / 'kanban' / 'boards').glob('*/board.json')):
                data = json.loads(meta.read_text())
                found[meta.parent.name] = {'slug': meta.parent.name, 'archived': bool(data.get('archived'))}
            return found
        return {b['slug']: b for b in json.loads(self.run('kanban', 'boards', 'list', '--json', '--all', mutate=False) or '[]')}

    def cron_jobs(self, profile):
        store = self.profile_dir(profile) / 'cron' / 'jobs.json'
        if not store.exists():
            return []
        data = json.loads(store.read_text())
        jobs = data.get('jobs', []) if isinstance(data, dict) else data
        return jobs


class Registry:
    def __init__(self, home):
        # Agent-owned runtime state. ($HERMES_HOME/nix is root-owned deployment metadata.)
        self.root = Path(home) / 'ventures'

    def path(self, vid):
        return self.root / vid

    def load(self, vid):
        manifest = self.path(vid) / 'manifest.json'
        return json.loads(manifest.read_text()) if manifest.exists() else None

    def save(self, manifest):
        current = self.load(manifest['id'])
        if current == manifest:
            return
        manifest['updated_at'] = now()
        atomic_write(self.path(manifest['id']) / 'manifest.json', json.dumps(manifest, indent=2) + '\n')

    def all(self):
        if not self.root.is_dir():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*/manifest.json'))]


def render(name, values):
    return string.Template((TEMPLATES / name).read_text()).substitute(values)


def loops(manifest):
    """Logical loop templates as native cron jobs owned by the lead profile."""
    vid = manifest['id']
    budget = manifest['budgets']
    guard = (f'Read /opt/data/ventures/{vid}/manifest.json first. Stop unless its lifecycle is '
             'incubating, operating, scaling or pivoting.')
    return [
        {'name': f'{vid}-outcome', 'schedule': '0 7 * * *',
         'prompt': (f'Venture outcome loop for {vid}. {guard} Read CHARTER.md, measure its outcome metrics '
                    'at their sources, and rank eligible Kanban cards with the Current constraint first. '
                    f'Release up to {budget["max_cards_per_window"]} via python3 {FACTORY} release '
                    f'--id {vid} TASK_ID [TASK_ID ...], in rank and dependency order. '
                    f'Reserve {budget["max_attempt_minutes"]} minutes per attempt with failure limit '
                    f'{budget["failure_limit"]}, up to {budget["max_work_minutes_per_window"]} work minutes '
                    f'per window, {budget["max_concurrent_workers"]} worker per lead and zero spend. '
                    'Leave unselected cards unassigned. Let native Kanban run the batch and advance dependencies. '
                    'Review the previous results and blockers on evidence. Queue founder-gated actions.')},
        {'name': f'{vid}-distill', 'schedule': '0 6 * * 1',
         'prompt': (f'Weekly workflow-distillation review for {vid}. {guard} Find a procedure that succeeded '
                    'at least twice. If a deterministic script, skill or workflow would remove repeated reasoning, '
                    'open an unassigned Kanban card for the outcome loop to rank. Do not change the metric '
                    'that judges it.')},
    ]


def hermes_root(home):
    """The registry is runtime-wide: resolve a named profile's home to the Hermes root."""
    path = Path(home)
    return str(path.parent.parent) if path.parent.name == 'profiles' else str(path)


def validate_id(vid):
    if not ID_RE.fullmatch(vid or '') or vid in RESERVED:
        raise Refusal('Venture id must be 2-24 lowercase letters/digits, start with a letter, and not be reserved')


@contextmanager
def venture_lock(registry, vid):
    """Serialize all operations on one venture (create, lifecycle) across processes."""
    directory = registry.path(vid)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def marker_ok(hermes, vid):
    directory, marker = hermes.profile_dir(vid), hermes.profile_dir(vid) / MARKER
    if directory.is_symlink() or not directory.is_dir() or marker.is_symlink() or not marker.is_file():
        return False
    try:
        return json.loads(marker.read_text()).get('venture') == vid
    except (OSError, json.JSONDecodeError):
        return False


def require_owned(hermes, manifest):
    if not marker_ok(hermes, manifest['lead_profile']):
        raise Refusal(f'Profile {manifest["lead_profile"]} is not the registry-owned lead; refusing to act on it')


def own_profile(hermes, registry, manifest):
    """Create the lead profile, or confirm this registry created it. Never adopt foreign profiles."""
    vid = manifest['id']
    directory = hermes.profile_dir(vid)
    if marker_ok(hermes, vid):
        return
    if directory.exists() or directory.is_symlink():
        claim = manifest.get('provisioning', {}).get('profile_claimed_at')
        # Crash between `profile create` and the marker write: adopt only a real directory that
        # appeared after this registry recorded its claim (legacy profiles predate any claim).
        marker = directory / MARKER
        if (claim and not directory.is_symlink() and directory.is_dir() and not marker.is_symlink()
                and not marker.exists()
                and directory.stat().st_mtime >= datetime.fromisoformat(claim).timestamp() - 1):
            atomic_write(directory / MARKER, json.dumps({'venture': vid, 'created_at': now()}) + '\n')
            return
        raise Refusal(f'Profile {vid} already exists and is not owned by the venture registry; refusing to adopt it')
    if hermes.dry_run:
        hermes.run('profile', 'create', vid)
        return
    manifest.setdefault('provisioning', {})['profile_claimed_at'] = now()
    registry.save(manifest)
    hermes.run('profile', 'create', vid, '--no-alias', '--description',
               f'Accountable lead for venture {manifest["name"]}: {manifest["mission"][:160]}')
    atomic_write(directory / MARKER, json.dumps({'venture': vid, 'created_at': now()}) + '\n')


def ensure_soul(hermes, manifest):
    directory = hermes.profile_dir(manifest['id'])
    soul = directory / 'SOUL.md'
    text = render('SOUL.md', {
        'id': manifest['id'], 'name': manifest['name'], 'mission': manifest['mission'],
        'registry': f'/opt/data/ventures/{manifest["id"]}', 'board': manifest['board'],
        'packs': ', '.join(manifest['packs']),
    })
    recorded = manifest.get('soul_sha256')
    current = sha256(soul.read_text()) if soul.exists() and not soul.is_symlink() else None
    if soul.is_symlink():
        raise Refusal('Lead SOUL.md is a symlink; refusing to write through it')
    if recorded and current not in (None, recorded):
        manifest.setdefault('drift', {})['soul'] = 'locally modified; left unchanged'
        return
    if hermes.dry_run:
        hermes.planned.append(['write', str(soul)])
        return
    atomic_write(soul, text, 0o644)
    manifest['soul_sha256'] = sha256(text)
    manifest.get('drift', {}).pop('soul', None)


def ensure_board(hermes, registry, manifest):
    slug = manifest['board']
    boards = hermes.boards()
    claim = manifest.get('provisioning', {}).get('board_claimed_at')
    if slug in boards and claim and not manifest['provisioning'].get('board_created'):
        created = boards[slug].get('created_at')
        if isinstance(created, (int, float)) and created >= datetime.fromisoformat(claim).timestamp() - 1:
            manifest['provisioning']['board_created'] = True  # crash after create, before recording
            registry.save(manifest)
    if slug in boards:
        if not manifest.get('provisioning', {}).get('board_created'):
            raise Refusal(f'Board {slug} already exists and was not created by this venture; refusing to reuse it')
        if boards[slug].get('archived'):
            raise Refusal(f'Board {slug} is archived; restore it deliberately before reuse')
        return
    if not hermes.dry_run:
        manifest.setdefault('provisioning', {})['board_claimed_at'] = now()
        registry.save(manifest)
    hermes.run('kanban', 'boards', 'create', slug, '--name', manifest['name'],
               '--description', f'Venture board for {manifest["name"]} (organizational, not an access boundary)')
    if not hermes.dry_run:
        manifest.setdefault('provisioning', {})['board_created'] = True
        registry.save(manifest)


def reconcile_budgets(manifest):
    budget = manifest.setdefault('budgets', {})
    for key, value in BUDGETS.items():
        budget.setdefault(key, value)
        if type(budget[key]) is not int or not (0 < budget[key] <= value or budget[key] == value == 0):
            raise Refusal(f'Invalid venture budget: {key}')
    return budget


def cron_enabled(job):
    return job.get('enabled', True) and job.get('state') != 'paused' and not job.get('paused_at')


def ensure_loops(hermes, registry, manifest, activate):
    """Reconcile factory jobs by name in place and record the verified spec for each job ID."""
    vid = manifest['id']
    reconcile_budgets(manifest)
    want = loops(manifest)
    manifest['loops'] = [loop['name'] for loop in want]
    for loop in want:
        name, schedule, prompt = (loop[key] for key in ('name', 'schedule', 'prompt'))
        tz = manifest.get('timezone')
        spec = (schedule, prompt, ['venture-lead'], tz, False)
        jobs = [job for job in hermes.cron_jobs(vid) if job.get('name') == name]
        if len(jobs) > 1:
            raise Refusal(f'Ambiguous venture cron job: {name}')
        job = jobs[0] if jobs else None
        flags = ['--skill', 'venture-lead', '--catch-up', 'off']
        changed = job is None or cron_spec(job) != spec
        if job is None:
            hermes.run('cron', 'create', schedule, prompt, '--name', name, *flags,
                       '--paused', '--paused-reason', 'venture provisioning',
                       *(['--timezone', tz] if tz else []), profile=vid)
        elif changed:
            hermes.run('cron', 'edit', job['id'], '--schedule', schedule, '--prompt', prompt,
                       *flags, '--timezone', tz or '', profile=vid)
        if hermes.dry_run:
            continue
        actual = next((j for j in hermes.cron_jobs(vid) if j.get('name') == name), None)
        if actual is None or cron_spec(actual) != spec:
            raise Refusal(f'Cron job {name} did not read back with the expected spec')
        receipt = {'job_id': actual['id'], 'spec_sha256': sha256(json.dumps(spec))}
        receipts = manifest.setdefault('loop_reconciliation', {})
        previous = receipts.get(name, {})
        if changed or any(previous.get(key) != value for key, value in receipt.items()):
            receipts[name] = dict(receipt, reconciled_at=now())
            registry.save(manifest)
        if activate and not cron_enabled(actual):
            hermes.run('cron', 'resume', actual['id'], profile=vid)
        elif not activate and cron_enabled(actual):
            hermes.run('cron', 'pause', actual['id'], profile=vid)


def cron_spec(job):
    return ((job.get('schedule') or {}).get('expr'), job.get('prompt'), job.get('skills'),
            job.get('timezone'), job.get('catch_up'))


def board_paths(hermes, manifest):
    slug = manifest['board']
    if slug != manifest['id'] or not manifest.get('provisioning', {}).get('board_created'):
        raise Refusal('Venture board ownership is required')
    root = hermes.home / 'kanban' / 'boards'
    return root / slug, root / '_archived' / f'nix-hold-{slug}'


def hold_board(hermes, registry, manifest):
    board, held = board_paths(hermes, manifest)
    if board.exists():
        if held.exists() or board.is_symlink() or not (board / 'kanban.db').is_file():
            raise Refusal('Venture board hold conflicts with existing board state')
        metadata = json.loads((board / 'board.json').read_text())
        if metadata.get('archived') and metadata.get('nix_venture_hold') != manifest['id']:
            raise Refusal('Venture board is already archived outside the factory')
        held.parent.mkdir(exist_ok=True)
        # Match Hermes' archive directory layout and serialize against its native dispatch tick.
        with (board / 'kanban.db.dispatch.lock').open('ab') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            # Stop new dispatcher scans before reclaiming existing claims.
            metadata['archived'] = True
            metadata['nix_venture_hold'] = manifest['id']
            atomic_write(board / 'board.json', json.dumps(metadata) + '\n')
            reclaim_running(hermes, manifest)
            board.rename(held)
    if not (held / 'kanban.db').is_file():
        raise Refusal('Venture board database is missing')
    # The archived directory is enough to exclude the board; no live metadata remains.
    receipt = str(held.relative_to(hermes.home))
    if manifest.get('held_board') != receipt:
        manifest['held_board'] = receipt
        registry.save(manifest)


def reclaim_running(hermes, manifest):
    def running():
        return json.loads(hermes.run('list', '--status', 'running', '--json',
                                     board=manifest['board'], mutate=False))

    for task in running():
        try:
            hermes.run('reclaim', task['id'], '--reason', 'venture lifecycle hold', board=manifest['board'])
        except Refusal:
            if task['id'] in {current['id'] for current in running()}:
                raise


def restore_board(hermes, manifest):
    board, held = board_paths(hermes, manifest)
    if held.exists():
        if board.exists() or held.is_symlink():
            raise Refusal('Venture board restore conflicts with existing board state')
        metadata = json.loads((held / 'board.json').read_text())
        if metadata.get('nix_venture_hold') != manifest['id']:
            raise Refusal('Venture board hold ownership is required')
        held.rename(board)
    path = board / 'board.json'
    metadata = json.loads(path.read_text())
    if metadata.get('nix_venture_hold') == manifest['id']:
        metadata.pop('nix_venture_hold')
        metadata['archived'] = False
        atomic_write(path, json.dumps(metadata) + '\n')
    manifest.pop('held_board', None)


def suspend_cron(hermes, registry, manifest):
    vid = manifest['id']
    jobs = hermes.cron_jobs(vid)
    custom = set(manifest.get('suspended_cron', []))
    managed = {loop['name'] for loop in loops(manifest)}
    custom.update(job['id'] for job in jobs if job.get('name') not in managed and cron_enabled(job))
    if custom:
        manifest['suspended_cron'] = sorted(custom)
        registry.save(manifest)
    for job in jobs:
        if cron_enabled(job):
            hermes.run('cron', 'pause', job['id'], profile=vid)
        hermes.run('cron', 'cancel', job['id'], '--reason', 'venture lifecycle hold', profile=vid)


def retire_profile(hermes, manifest):
    profile = hermes.profile_dir(manifest['lead_profile'])
    parked = profile / 'gateway.parked'
    if not parked.exists():
        # Hermes' multiplexer and ingress routes honor the marker on rescan and restart.
        atomic_write(parked, '')
    env = profile / '.env'
    if env.is_symlink():
        raise Refusal('Retired profile .env is a symlink')
    if env.exists():
        with env.open(newline='') as handle:
            current = handle.read()
        kept = ''.join(line for line in current.splitlines(keepends=True) if not RETIRED_KEYS.match(line))
        if kept != current:
            atomic_write(env, kept)


def reconcile_lifecycle(hermes, registry, manifest):
    state = manifest['lifecycle']
    reconcile_budgets(manifest)
    if state in {'paused', 'retired'}:
        hold_board(hermes, registry, manifest)
        if state == 'retired':
            retire_profile(hermes, manifest)
        suspend_cron(hermes, registry, manifest)
    elif state in ACTIVE:
        ensure_loops(hermes, registry, manifest, activate=True)
        for job in hermes.cron_jobs(manifest['id']):
            if job['id'] in manifest.get('suspended_cron', []) and not cron_enabled(job):
                hermes.run('cron', 'resume', job['id'], profile=manifest['id'])
        manifest.pop('suspended_cron', None)
        restore_board(hermes, manifest)
    else:
        raise Refusal('Venture provisioning is incomplete; retry create')


def release(hermes, registry, vid, ranked):
    """Admit a ranked batch, reserving its worst-case minutes in native task events."""
    with venture_lock(registry, vid):
        manifest = registry.load(vid)
        if not manifest or manifest['lifecycle'] not in ACTIVE or manifest.get('held_board'):
            raise Refusal('Venture is not admitting work')
        require_owned(hermes, manifest)
        budget = reconcile_budgets(manifest)
        config = json.loads(hermes.run('config', 'get', 'kanban', '--json', profile='default', mutate=False))
        if (config.get('max_in_progress_per_profile') != budget['max_concurrent_workers']
                or config.get('max_in_progress') not in (1, 2) or config.get('default_assignee')):
            raise Refusal('Set a shared Kanban cap of 1 or 2, one worker per profile and no default assignee, '
                          'then reload the gateway before releasing work')
        zone = ZoneInfo(manifest.get('timezone') or 'UTC')
        window = datetime.now(zone).date().isoformat()
        # Use read/write mode so a missing or held board can never become an empty live board.
        board, _ = board_paths(hermes, manifest)
        db = sqlite3.connect((board / 'kanban.db').as_uri() + '?mode=rw', uri=True)
        try:
            db.row_factory = sqlite3.Row
            with db:
                db.execute('BEGIN IMMEDIATE')
                return release_batch(db, manifest['lead_profile'], window, budget, ranked)
        finally:
            db.close()


def release_batch(db, lead, window, budget, ranked):
    cards = {row['id']: row for row in db.execute(
        'SELECT id, body, status, assignee, priority FROM tasks')}
    open_states = {'ready', 'todo', 'running', 'review'}
    reservations = {}
    for event in db.execute("SELECT task_id, payload FROM task_events WHERE kind = 'venture_released'"):
        reservations[event['task_id']] = json.loads(event['payload'])
    charged = [record for tid, record in reservations.items()
               if record['window'] == window or (tid in cards and cards[tid]['status'] in open_states)]
    minutes = budget['max_attempt_minutes'] * budget['failure_limit']
    remaining = min(budget['max_cards_per_window'] - len(charged),
                    (budget['max_work_minutes_per_window'] - sum(r['minutes'] for r in charged)) // minutes)
    # Existing assignments can drain before this window reserves new work.
    if any(card['assignee'] and card['status'] in open_states and tid not in reservations
           for tid, card in cards.items()):
        return {'window': window, 'released': [], 'reason': 'existing assigned work'}

    candidates = {tid for tid, card in cards.items()
                  if card['status'] in {'ready', 'todo'} and not card['assignee'] and tid not in reservations}
    constraints = sorted((tid for tid in candidates if CONSTRAINT.search(cards[tid]['body'] or '')),
                         key=lambda tid: (-cards[tid]['priority'], tid))
    parents = {}
    for link in db.execute('SELECT parent_id, child_id FROM task_links'):
        parents.setdefault(link['child_id'], []).append(link['parent_id'])
    viable = {tid for tid, card in cards.items()
              if card['status'] in {'done', 'archived'} or (tid in reservations and card['status'] in open_states)}
    released = []
    priority = max((card['priority'] for card in cards.values()), default=0) + max(remaining, 0)
    for tid in dict.fromkeys([*constraints, *ranked]):
        if len(released) >= remaining:
            break
        if tid not in candidates or not all(parent in viable for parent in parents.get(tid, [])):
            continue
        # The pinned CLI has no edit flags for attempt limits on existing cards. Commit the
        # native assignment, limits and reservation together before the dispatcher can claim.
        db.execute('UPDATE tasks SET assignee=?, max_runtime_seconds=?, max_retries=?, priority=? WHERE id=?',
                   (lead, 60 * budget['max_attempt_minutes'], budget['failure_limit'], priority, tid))
        db.execute("INSERT INTO task_events(task_id, kind, payload, created_at) VALUES (?, 'venture_released', ?, ?)",
                   (tid, json.dumps({'window': window, 'minutes': minutes, 'assignee': lead}),
                    int(datetime.now(timezone.utc).timestamp())))
        released.append(tid)
        viable.add(tid)
        priority -= 1
    return {'window': window, 'released': released}


def ensure_mission(hermes, registry, manifest):
    if manifest.get('first_task'):
        return  # created once; reruns never reopen or duplicate it
    vid = manifest['id']
    body = render('MISSION.md', {'id': vid, 'name': manifest['name'], 'mission': manifest['mission'],
                                 'registry': f'/opt/data/ventures/{vid}'})
    out = hermes.run('create', f'Mission 1: {manifest["mission"][:80]}', '--body', body,
                     '--idempotency-key', f'venture:{vid}:mission:1', '--created-by', 'venture-factory',
                     '--json', board=manifest['board'])
    if hermes.dry_run:
        return
    try:
        task = json.loads(out).get('id')
    except (json.JSONDecodeError, AttributeError):
        task = None
    if not task:
        raise Refusal('Mission card creation returned no task id')
    manifest['first_task'] = task
    registry.save(manifest)


def create(args, hermes, registry):
    validate_id(args.id)
    packs = sorted(set(CORE_PACKS) | set(args.pack or DEFAULT_PACKS))
    if unknown := set(packs) - PACKS:
        raise Refusal('Unknown packs: ' + ', '.join(sorted(unknown)))
    if not (args.approval_ref or '').strip():
        raise Refusal('Founder strategic approval reference is required (message link or decision note)')
    documents = decision_package(registry, args)  # validated before any side effect
    if hermes.dry_run:
        return plan(args, hermes, registry, packs)
    with venture_lock(registry, args.id):
        existing = registry.load(args.id)  # reloaded under the lock: never act on stale state
        if existing and existing['lifecycle'] == 'retired':
            raise Refusal('Venture is retired; history is preserved and it is not re-created')
        if existing and (existing['name'], existing['mission']) != (args.name, args.mission):
            raise Refusal('A venture with this id exists with a different name/mission; use stage/pivot instead')
        profile = hermes.profile_dir(args.id)
        if existing is None and (profile.exists() or profile.is_symlink()):
            raise Refusal(f'Profile {args.id} already exists and is not owned by the venture registry; refusing to adopt it')
        manifest = existing or new_manifest(args, packs)
        directory = registry.path(args.id)
        if existing is None:
            registry.save(manifest)  # intent is durable before any native side effect
        for name, text in documents.items():
            current = (directory / name).read_text() if (directory / name).is_file() else None
            if current != text:
                atomic_write(directory / name, text)
        if not (directory / 'CHARTER.md').exists():
            atomic_write(directory / 'CHARTER.md', render('CHARTER.md', {
                'id': args.id, 'name': args.name, 'mission': args.mission,
                'approval': manifest['approval']['ref'], 'packs': ', '.join(packs),
                'crm': 'yes' if args.crm else 'not needed yet'}))
        own_profile(hermes, registry, manifest)
        ensure_soul(hermes, manifest)
        registry.save(manifest)
        if manifest['lifecycle'] == 'paused':
            reconcile_lifecycle(hermes, registry, manifest)
            registry.save(manifest)
            return manifest
        if not manifest.get('held_board'):
            ensure_board(hermes, registry, manifest)
        if manifest['lifecycle'] == 'approved':
            ensure_loops(hermes, registry, manifest, activate=False)  # pre-commit: created paused
        ensure_mission(hermes, registry, manifest)
        if manifest['lifecycle'] == 'approved':
            manifest['lifecycle'] = 'incubating'
            manifest['history'].append({'at': now(), 'to': 'incubating', 'by': 'venture-factory'})
            manifest.pop('provisioning_incomplete', None)
            registry.save(manifest)  # commit point: routines start only after this
        reconcile_lifecycle(hermes, registry, manifest)
        registry.save(manifest)
        return manifest


def decision_package(registry, args):
    """The exact PRD/GTM text the lead will read: a non-empty registry copy wins (reruns need no
    scratch files); otherwise the supplied file must be non-empty."""
    documents = {}
    for name, source in (('PRD.md', args.prd), ('GTM.md', args.gtm)):
        held = registry.path(args.id) / name
        text = held.read_text() if held.is_file() and not held.is_symlink() else ''
        if not text.strip() and source and Path(source).is_file():
            text = Path(source).read_text()
        if not text.strip():
            raise Refusal(f'The approved {name[:-3]} is missing or empty: provision only from a decision package')
        documents[name] = text
    return documents


def new_manifest(args, packs):
    return {
        'schema_version': SCHEMA_VERSION, 'id': args.id, 'name': args.name, 'mission': args.mission,
        'lifecycle': 'approved', 'lead_profile': args.id, 'board': args.id, 'packs': packs,
        'approval': {'ref': args.approval_ref.strip(), 'recorded_at': now()},
        'memory': {'provider': 'hindsight', 'bank': f'venture-{args.id}', 'status': 'pending'},
        'integrations': {'crm': {'enabled': bool(args.crm), 'workspace': None},
                         'github_repo': args.repo, 'knowledge_folder': f'Ventures/{args.id}'},
        'budgets': dict(BUDGETS), 'timezone': args.timezone, 'created_at': now(), 'history': [], 'provisioning': {},
    }


def plan(args, hermes, registry, packs):
    """Dry run: report the native actions without writing anything."""
    existing = registry.load(args.id)
    manifest = existing or new_manifest(args, packs)
    if existing and existing['lifecycle'] == 'retired':
        raise Refusal('Venture is retired; history is preserved and it is not re-created')
    directory = hermes.profile_dir(args.id)
    if (directory.exists() or directory.is_symlink()) and not marker_ok(hermes, args.id):
        raise Refusal(f'Profile {args.id} already exists and is not owned by the venture registry; refusing to adopt it')
    if not directory.exists():
        hermes.run('profile', 'create', args.id)
    ensure_soul(hermes, manifest)
    ensure_board(hermes, registry, manifest)
    ensure_loops(hermes, registry, manifest, activate=False)
    ensure_mission(hermes, registry, manifest)
    return {'dry_run': True, 'planned': [' '.join(c[1:6]) for c in hermes.planned]}


def transition(args, hermes, registry, target):
    with venture_lock(registry, args.id):
        manifest = registry.load(args.id)
        if manifest is None:
            raise Refusal('Unknown venture')
        require_owned(hermes, manifest)
        state = manifest['lifecycle']
        if target is None or target == state or (target == 'resume' and state in ACTIVE):
            reconcile_lifecycle(hermes, registry, manifest)
            registry.save(manifest)
            return manifest
        if state == 'retired':
            raise Refusal('Venture is retired')
        if target == 'resume':
            if state != 'paused':
                raise Refusal('Only a paused venture can resume')
            target = manifest.get('resume_to', 'incubating')
        elif target == 'paused':
            if state not in ACTIVE:
                raise Refusal('Only an active venture can pause')
            manifest['resume_to'] = state
        elif target in ('operating', 'scaling', 'pivoting') and state not in ACTIVE:
            raise Refusal('Resume the venture before changing its stage')
        manifest['lifecycle'] = target
        manifest['history'].append({'at': now(), 'to': target, 'reason': getattr(args, 'reason', None)})
        registry.save(manifest)  # the decision is durable first; a failed cron call is retried
        reconcile_lifecycle(hermes, registry, manifest)
        registry.save(manifest)
        return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--home', default=hermes_root(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))))
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create')
    c.add_argument('--id', required=True)
    c.add_argument('--name', required=True)
    c.add_argument('--mission', required=True, help='First measurable commercial/operational uncertainty')
    c.add_argument('--approval-ref', required=True)
    # Required: leads judge every loop against the approved PRD/GTM. Observed 2026-11-23: a lead
    # provisioned without them had no source-of-truth metric and correctly blocked its first mission.
    c.add_argument('--prd', help='approved PRD file (required unless the registry already holds a non-empty copy)')
    c.add_argument('--gtm', help='approved GTM file (required unless the registry already holds a non-empty copy)')
    c.add_argument('--pack', action='append')
    c.add_argument('--crm', action='store_true')
    c.add_argument('--repo')
    c.add_argument('--timezone')
    c.add_argument('--dry-run', action='store_true')
    for name in ('pause', 'resume', 'retire'):
        p = sub.add_parser(name)
        p.add_argument('--id', required=True)
        p.add_argument('--reason')
    s = sub.add_parser('stage')
    s.add_argument('--id', required=True)
    s.add_argument('--to', required=True, choices=['operating', 'scaling', 'pivoting'])
    s.add_argument('--reason')
    rec = sub.add_parser('reconcile')
    rec.add_argument('--id', required=True)
    rel = sub.add_parser('release')
    rel.add_argument('--id', required=True)
    rel.add_argument('tasks', nargs='+')
    st = sub.add_parser('status')
    st.add_argument('--id')
    args = parser.parse_args(argv)
    hermes = Hermes(args.home, dry_run=getattr(args, 'dry_run', False))
    registry = Registry(args.home)
    try:
        if args.command == 'create':
            result = create(args, hermes, registry)
        elif args.command == 'status':
            result = registry.load(args.id) if args.id else [
                {k: v[k] for k in ('id', 'name', 'lifecycle', 'board')} for v in registry.all()]
        elif args.command == 'release':
            validate_id(args.id)
            result = release(hermes, registry, args.id, args.tasks)
        else:
            validate_id(args.id)
            target = {'reconcile': None, 'pause': 'paused', 'resume': 'resume', 'retire': 'retired', 'stage': None}[args.command]
            result = transition(args, hermes, registry, target if args.command != 'stage' else args.to)
    except (Refusal, OSError, json.JSONDecodeError, KeyError, sqlite3.Error) as exc:
        print(f'venture: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
