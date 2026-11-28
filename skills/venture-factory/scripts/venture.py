#!/usr/bin/env python3
"""Generic, idempotent venture lifecycle for one multi-profile Hermes runtime.

Runs inside the Hermes container (HERMES_HOME). The registry is native runtime state at
$HERMES_HOME/ventures/<id>/; Git holds only this tool and its templates. Every native
change goes through the `hermes` CLI; profile cron stores are only read back.

  venture.py create  --id ID --name NAME --mission TEXT --approval-ref REF [--prd F] [--gtm F]
                     [--pack P ...] [--crm] [--repo OWNER/NAME] [--timezone TZ] [--dry-run]
  venture.py pause|resume|retire --id ID [--reason TEXT]
  venture.py stage   --id ID --to operating|scaling|pivoting
  venture.py status  [--id ID]

No venture name is built in. A pre-existing profile that this registry did not create is
never adopted, so historical/legacy profiles cannot be reactivated by a new venture.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import string
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / 'templates'
GATE_SOURCE = HERE.parent.parent / 'company-ops' / 'scripts' / 'collect_evidence.py'
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
        self.home = Path(home)
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
            return {}
        data = json.loads(store.read_text())
        jobs = data.get('jobs', []) if isinstance(data, dict) else data
        return {j.get('name'): j for j in jobs if j.get('name')}


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
        manifest['updated_at'] = now()
        atomic_write(self.path(manifest['id']) / 'manifest.json', json.dumps(manifest, indent=2) + '\n')

    def all(self):
        if not self.root.is_dir():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*/manifest.json'))]


def render(name, values):
    return string.Template((TEMPLATES / name).read_text()).substitute(values)


def loops(vid, tz):
    """Logical loop templates as native cron jobs owned by the lead profile.

    Each carries a pre-run idle gate (`script`, a copy of the evidence collector in the profile's own
    scripts/nix-os/): Hermes skips the agent, and its tokens, when the gate prints {"wakeAgent": false}.
    """
    return [
        {'name': f'{vid}-outcome', 'schedule': '0 7 * * *', 'script': 'nix-os/gate-outcome.py',
         'skills': ['venture-lead'],
         'prompt': (f'Venture outcome loop for {vid}. Read your CHARTER and the venture board. '
                    'Observe source-system evidence, pick the single highest-value next task toward the '
                    'mission metric, execute or delegate it (Kanban card; T3 for code), verify on evidence, '
                    'record the result and any blocker. Queue privileged external actions; never perform them. '
                    'Work the open cards or approvals in the evidence bundle above; on a "charter review due" wake, '
                    'plan one next card or record that nothing is worth doing.')},
        {'name': f'{vid}-distill', 'schedule': '0 6 * * 1', 'script': 'nix-os/gate-distill.py',
         'skills': ['venture-lead'],
         'prompt': (f'Weekly workflow-distillation review for {vid}. Find a procedure that succeeded at least '
                    'twice. If a deterministic script, skill or workflow would remove repeated reasoning, open '
                    'a Kanban card to distill it through a PR with tests. Deterministic-first: a correction that '
                    'recurs (>=2 sanitized examples in the evidence bundle above) gets a script, CI check, lint or '
                    'API constraint before any skill prose. Never auto-apply; never edit the tests or metric that '
                    'judge your own change in the same PR.')},
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


def install_gates(hermes, vid, want):
    """Place the idle-gate scripts in the lead profile's own scripts/nix-os/ (cron resolves scripts only there
    and refuses to create or edit a job whose script file does not exist). The deployment refreshes these
    copies. Returns False, leaving the loops ungated, when the collector or a safe target is unavailable."""
    if not GATE_SOURCE.is_file():
        return False
    target = hermes.profile_dir(vid) / 'scripts' / 'nix-os'
    if any(p.is_symlink() for p in (target.parent, target)):
        return False
    if not hermes.dry_run:
        text = GATE_SOURCE.read_text()
        for loop in want:
            path = target / Path(loop['script']).name
            if not path.is_file() or path.read_text() != text:
                atomic_write(path, text, 0o755)
    return True


def ensure_loops(hermes, registry, manifest, activate):
    """Create missing loops PAUSED (recorded immediately); resume them only when `activate`."""
    vid = manifest['id']
    want = loops(vid, manifest.get('timezone'))
    gated = install_gates(hermes, vid, want)
    manifest['loops'] = [l['name'] for l in want]
    jobs = hermes.cron_jobs(vid)
    for loop in want:
        if loop['name'] in jobs:
            continue
        argv = ['cron', 'create', loop['schedule'], loop['prompt'], '--name', loop['name'], '--catch-up', 'off',
                '--paused', '--paused-reason', 'venture provisioning']
        for skill in loop['skills']:
            argv += ['--skill', skill]
        if gated:
            argv += ['--script', loop['script']]
        if manifest.get('timezone'):
            argv += ['--timezone', manifest['timezone']]
        hermes.run(*argv, profile=vid)
        if not hermes.dry_run:
            registry.save(manifest)
    jobs = hermes.cron_jobs(vid)
    for loop in want:
        job = jobs.get(loop['name'])
        if job is None:
            if hermes.dry_run:
                continue
            raise Refusal(f'Cron job {loop["name"]} did not read back after creation')
        if gated and job.get('script') != loop['script']:
            hermes.run('cron', 'edit', job['id'], '--script', loop['script'], profile=vid)  # idle gate only
        is_paused = job.get('paused_at') is not None or job.get('state') == 'paused' or not job.get('enabled', True)
        if activate and is_paused:
            hermes.run('cron', 'resume', job['id'], profile=vid)
        elif not activate and not is_paused:
            hermes.run('cron', 'pause', job['id'], profile=vid)


def ensure_mission(hermes, registry, manifest):
    if manifest.get('first_task'):
        return  # created once; reruns never reopen or duplicate it
    vid = manifest['id']
    body = render('MISSION.md', {'id': vid, 'name': manifest['name'], 'mission': manifest['mission'],
                                 'registry': f'/opt/data/ventures/{vid}'})
    out = hermes.run('create', f'Mission 1: {manifest["mission"][:80]}', '--body', body, '--assignee', vid,
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
        ensure_board(hermes, registry, manifest)
        if manifest['lifecycle'] == 'approved':
            ensure_loops(hermes, registry, manifest, activate=False)  # pre-commit: created paused
        ensure_mission(hermes, registry, manifest)
        if manifest['lifecycle'] == 'approved':
            manifest['lifecycle'] = 'incubating'
            manifest['history'].append({'at': now(), 'to': 'incubating', 'by': 'venture-factory'})
            manifest.pop('provisioning_incomplete', None)
            registry.save(manifest)  # commit point: routines start only after this
        ensure_loops(hermes, registry, manifest, activate=manifest['lifecycle'] in ACTIVE)
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
        'timezone': args.timezone, 'created_at': now(), 'history': [], 'provisioning': {},
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
        if target == state or (target == 'resume' and state in ACTIVE):
            # Retry of a decision already recorded (e.g. a cron call failed): reconcile routines only.
            ensure_loops(hermes, registry, manifest, activate=state in ACTIVE)
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
        ensure_loops(hermes, registry, manifest, activate=target in ACTIVE)
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
        else:
            target = {'pause': 'paused', 'resume': 'resume', 'retire': 'retired', 'stage': None}[args.command]
            result = transition(args, hermes, registry, target or args.to)
    except (Refusal, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f'venture: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
