#!/usr/bin/env python3
"""Evidence collector and idle gate for the improvement and distillation loops. Stdlib only, no LLM.

Runs as the pre-run `--script` of a Hermes agent cron job. Hermes skips the agent entirely (no LLM
call) when the LAST non-empty stdout line is exactly {"wakeAgent": false}. Anything else wakes it,
so every failure here is fail-open: a missing source or an internal error never silences a loop.

Scripts run from the owning profile's `scripts/` and take no arguments, so the loop is chosen by the
installed file name (gate-outcome.py, gate-distill.py or gate-improvement.py) and the profile by its location. Manual use:
  collect_evidence.py [--gate outcome|distill|improvement] [--home DIR] [--now ISO] [--github OWNER/REPO]

Sources (all local, bounded, degrading to "unavailable"; formats checked against Hermes v0.23.0):
  cron/jobs.json, cron/usage_audit.jsonl, skills/.usage.json (root and per-profile), ventures/*/manifest.json
  and actions/*.json, Kanban via `hermes kanban --board B list|show --json` (the CLI refreshes ready-state,
  so this is not strictly read-only), and optionally two public GitHub REST calls for the improvement gate.
Untrusted card text is reduced to short lowercase signatures; raw titles and bodies never reach the prompt.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

SCHEMA = 1
WINDOW_DAYS = 14
HEARTBEAT_DAYS = 7          # outcome loop: a quiet venture still reviews its charter this often
MIN_REPEAT = 2              # a pattern or correction counts once it recurs this often
FAIL_STREAK = 2             # consecutive cron failures that count as a repeated problem
MAX_SHOW = 30               # Kanban cards inspected for review corrections
BUDGET_SECONDS = 45         # whole-run deadline, far below the Hermes script timeout
MAX_ITEMS = 5
OPEN = ('triage', 'todo', 'scheduled', 'ready', 'running', 'review')  # blocked waits for input: not agent work
FAILURE_EVENTS = ('gave_up', 'crashed', 'spawn_failed', 'timed_out')
LOCAL_APPROVAL = ('queued-local', 'submitting')
KINDS = ('outcome', 'distill', 'improvement')
DEFAULT_REPO = 'its-nix/os'
UNSAFE_WORDS = {'ignore', 'disregard', 'instruction', 'instructions', 'system', 'prompt', 'override', 'jailbreak'}
INSTRUCTIONS = {
    'outcome': 'Work only the open cards or approvals listed; do not invent work.',
    'distill': ('Deterministic-first: for each repeated correction (>=2 sanitized examples) propose a script, '
                'CI check, lint or API constraint before any skill prose. Changes go through a T3 PR, never '
                'auto-applied. Never edit the tests or evaluator that judge your own change in the same PR.'),
    'improvement': ('Pick at most one pattern from repeated_patterns, repeated_corrections, failing_crons or '
                    'github. Deterministic-first as for distill; PR via T3 only; metric fixed before the change.'),
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, RecursionError):
        return None


def parse_ts(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def signature(text, words=8):
    """Short lowercase letters-only fingerprint of untrusted text (ids, numbers, urls, emails removed)."""
    text = re.sub(r'https?://\S+|\S+@\S+|\b[0-9a-f]{7,}\b|\bt_\w+', ' ', str(text or '').lower())
    tokens = [t[:16] for t in re.findall(r'[a-z]{3,}', text) if t not in UNSAFE_WORDS]
    return ' '.join(tokens[:words])


def default_home():
    """The profile that owns this installed copy (<home>/scripts/nix-os/gate-*.py), else $HERMES_HOME."""
    here = Path(__file__).resolve()
    if here.parent.name == 'nix-os' and here.parent.parent.name == 'scripts':
        return here.parents[2]
    return Path(os.environ.get('HERMES_HOME', '/opt/data'))


def roots(home):
    """(root, [(profile, profile_home), ...]) — the default profile first; named profiles beside it."""
    home = Path(home)
    root = home.parent.parent if home.parent.name == 'profiles' else home
    named = sorted(p for p in (root / 'profiles').glob('*') if p.is_dir()) if (root / 'profiles').is_dir() else []
    return root, [('default', root)] + [(p.name, p) for p in named]


def cron_sources(homes, now):
    jobs, failing, names = [], [], {}
    for profile, phome in homes:
        data = read_json(phome / 'cron' / 'jobs.json')
        for job in (data.get('jobs', []) if isinstance(data, dict) else data or []):
            if not isinstance(job, dict) or not job.get('name'):
                continue
            names[(profile, job.get('id'))] = job['name']
            jobs.append((profile, job))
            streak = job.get('failure_streak') if isinstance(job.get('failure_streak'), int) else 0
            if streak >= FAIL_STREAK and job.get('enabled', True):
                failing.append({'job': job['name'], 'profile': profile, 'streak': streak,
                                'error': signature(job.get('last_error'))})
    summary = {'jobs': len(jobs), 'enabled': sum(1 for _, j in jobs if j.get('enabled', True))}
    return summary, sorted(failing, key=lambda f: (-f['streak'], f['profile'], f['job']))[:MAX_ITEMS], names


def usage_sources(homes, names, now):
    """Aggregate cron/usage_audit.jsonl (one row per real agent fire) over the window."""
    cutoff = now - timedelta(days=WINDOW_DAYS)
    by_job, last_fire, status = {}, {}, 'unavailable'
    for profile, phome in homes:
        path = phome / 'cron' / 'usage_audit.jsonl'
        try:
            lines = path.read_text(encoding='utf-8').splitlines()[-2000:]
        except OSError:
            continue
        status = 'ok'
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(row.get('ts')) if isinstance(row, dict) else None
            if ts is None:
                continue
            name = names.get((profile, row.get('job_id')), 'unknown')
            if ts > last_fire.get((profile, name), ts - timedelta(seconds=1)):
                last_fire[(profile, name)] = ts
            if ts < cutoff:
                continue
            tokens = row.get('total_tokens') if isinstance(row.get('total_tokens'), int) else 0
            item = by_job.setdefault((profile, name), {'job': name, 'profile': profile, 'runs': 0, 'tokens': 0,
                                                       'silent_runs': 0, 'silent_tokens': 0, 'errors': 0})
            item['runs'] += 1
            item['tokens'] += tokens
            item['errors'] += 1 if row.get('error') else 0
            if row.get('response_silent'):
                item['silent_runs'] += 1
                item['silent_tokens'] += tokens
    rows = sorted(by_job.values(), key=lambda r: (-r['tokens'], r['profile'], r['job']))
    total = {'runs': sum(r['runs'] for r in rows), 'tokens': sum(r['tokens'] for r in rows),
             'silent_tokens': sum(r['silent_tokens'] for r in rows)}
    return {'status': status, 'window_days': WINDOW_DAYS, 'total': total, 'by_job': rows[:MAX_ITEMS]}, last_fire


def skill_sources(homes):
    skills, status = {}, 'unavailable'
    for _, phome in homes:
        data = read_json(phome / 'skills' / '.usage.json')
        if not isinstance(data, dict):
            continue
        status = 'ok'
        for name, rec in data.items():
            if isinstance(rec, dict) and re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', str(name)):
                item = skills.setdefault(name, {'skill': name, 'use': 0, 'view': 0, 'patch': 0})
                for key, field in (('use', 'use_count'), ('view', 'view_count'), ('patch', 'patch_count')):
                    item[key] += rec.get(field) if isinstance(rec.get(field), int) else 0
    rows = sorted(skills.values(), key=lambda r: (-(r['use'] + r['view'] + r['patch']), r['skill']))
    return {'status': status, 'top': rows[:MAX_ITEMS]}


def venture_sources(root, only=None):
    """Active ventures (id, board), their approval receipts by status and the count not yet delivered."""
    ventures, undelivered, receipts = [], {}, {}
    for manifest in sorted((root / 'ventures').glob('*/manifest.json')):
        data = read_json(manifest)
        vid, board = (data or {}).get('id'), (data or {}).get('board')
        if not (isinstance(vid, str) and isinstance(board, str)) or data.get('lifecycle') in ('paused', 'retired'):
            continue
        if only and vid != only:
            continue
        ventures.append((vid, board))
        for action in sorted((manifest.parent / 'actions').glob('*.json')):
            status = (read_json(action) or {}).get('status')
            if isinstance(status, str) and re.fullmatch(r'[a-z-]{1,24}', status):
                counts = receipts.setdefault(vid, {})
                counts[status] = counts.get(status, 0) + 1
                if status in LOCAL_APPROVAL:
                    undelivered[vid] = undelivered.get(vid, 0) + 1
    return ventures, undelivered, receipts


def hermes_json(*argv):
    result = subprocess.run([os.environ.get('HERMES_BIN', 'hermes'), 'kanban', *argv, '--json'],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('kanban')
    return json.loads(result.stdout or '[]')


def board_evidence(board, now, deadline, list_tasks, show_task):
    """Counts, repeated successful card patterns and repeated review corrections for one board."""
    try:
        tasks = [t for t in list_tasks(board) if isinstance(t, dict)]
    except Exception:  # unreadable board: report it, never fail
        return {'board': board, 'status': 'unavailable'}, [], []
    counts, done = {}, []
    for task in tasks:
        counts[task.get('status')] = counts.get(task.get('status'), 0) + 1
        finished = parse_ts(task.get('completed_at'))
        if task.get('status') == 'done' and (finished is None or now - finished <= timedelta(days=WINDOW_DAYS)):
            done.append(task)
    groups = {}
    for task in done:
        sig = signature(task.get('title'))
        if sig:
            groups.setdefault(sig, []).append(str(task.get('id')))
    distilled = [signature(t.get('title')) for t in tasks if 'distill' in str(t.get('title', '')).lower()]
    patterns = [{'board': board, 'signature': sig, 'count': len(ids), 'cards': sorted(ids)[:3]}
                for sig, ids in groups.items() if len(ids) >= MIN_REPEAT
                and not any(set(sig.split()) <= set(d.split()) for d in distilled)]
    corrections, failures, partial = {}, {}, False
    newest = sorted((t for t in tasks if t.get('status') != 'archived'), key=lambda t: (-(t.get('created_at') or 0), str(t.get('id'))))
    for task in newest[:MAX_SHOW]:
        if time.monotonic() > deadline:
            partial = True
            break
        try:
            events = (show_task(board, task['id']) or {}).get('events', [])
        except Exception:
            partial = True
            continue
        for event in events if isinstance(events, list) else []:
            kind = event.get('kind') if isinstance(event, dict) else None
            if kind in FAILURE_EVENTS:
                failures[kind] = failures.get(kind, 0) + 1
            if kind == 'changes_requested':
                payload = event.get('payload')
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except ValueError:
                        payload = {'reason': payload}
                text = ' '.join(str(payload.get(k, '')) for k in ('reason', 'comment', 'summary', 'message')) \
                    if isinstance(payload, dict) else ''
                sig = signature(text)
                if sig:
                    corrections.setdefault(sig, set()).add(str(task['id']))
    repeated = [{'board': board, 'signature': sig, 'count': len(ids), 'cards': sorted(ids)[:3]}
                for sig, ids in corrections.items() if len(ids) >= MIN_REPEAT]
    info = {'board': board, 'status': 'partial' if partial else 'ok', 'counts': dict(sorted(counts.items(), key=lambda kv: str(kv[0]))),
            'open': sum(counts.get(s, 0) for s in OPEN), 'failure_events': dict(sorted(failures.items()))}
    return info, patterns, repeated


def fetch_github(repo, now):
    """Two unauthenticated public REST calls; any failure marks the source unavailable."""
    def get(path):
        request = urllib.request.Request(f'https://api.github.com/repos/{repo}/{path}',
                                         headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'nix-os-evidence'})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed https host
            return json.loads(response.read(1_000_000))
    cutoff = now - timedelta(days=WINDOW_DAYS)
    pulls = get('pulls?state=closed&sort=updated&direction=desc&per_page=30')
    runs = get('actions/runs?status=completed&per_page=30').get('workflow_runs', [])
    recent = [p for p in pulls if (parse_ts(p.get('closed_at')) or cutoff) >= cutoff]
    failed = [r for r in runs if r.get('conclusion') == 'failure' and (parse_ts(r.get('created_at')) or cutoff) >= cutoff]
    return {'status': 'ok', 'repo': repo, 'prs_merged': sum(1 for p in recent if p.get('merged_at')),
            'prs_closed_unmerged': sum(1 for p in recent if not p.get('merged_at')),
            'ci_runs': len([r for r in runs if (parse_ts(r.get('created_at')) or cutoff) >= cutoff]),
            'ci_failures': len(failed), 'ci_failure_workflows': sorted({signature(r.get('name'), 4) for r in failed})[:MAX_ITEMS]}


def collect(home, kind, now=None, list_tasks=None, show_task=None, github=None, repo=None):
    """Build the bounded evidence bundle and the idle-gate decision for one loop kind."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    root, homes = roots(home)
    mine = Path(home).name if Path(home).parent.name == 'profiles' else None
    scope = [h for h in homes if mine is None or h[1] == Path(home)] if kind != 'improvement' else homes
    deadline = time.monotonic() + BUDGET_SECONDS
    list_tasks = list_tasks or (lambda board: hermes_json('--board', board, 'list'))
    show_task = show_task or (lambda board, task: hermes_json('--board', board, 'show', task))
    ventures, undelivered, receipts = venture_sources(root, only=mine if kind != 'improvement' else None)
    boards = sorted({b for _, b in ventures} | ({'default'} if kind == 'improvement' else set()))
    cron, failing, names = cron_sources(homes, now)
    usage, last_fire = usage_sources(scope, names, now)
    boards_info, patterns, corrections = [], [], []
    for board in boards[:12]:
        info, found, repeated = board_evidence(board, now, deadline, list_tasks, show_task)
        boards_info.append(info)
        patterns += found
        corrections += repeated
    bundle = {'schema': SCHEMA, 'gate': kind, 'generated_at': now.isoformat(), 'window_days': WINDOW_DAYS,
              'instruction': INSTRUCTIONS[kind], 'cron': dict(cron, failing=failing), 'usage': usage,
              'skills': skill_sources(scope), 'boards': boards_info, 'approvals': {v: dict(sorted(c.items())) for v, c in sorted(receipts.items())},
              'approvals_undelivered': dict(sorted(undelivered.items())),
              'repeated_patterns': sorted(patterns, key=lambda p: (-p['count'], p['board'], p['signature']))[:MAX_ITEMS],
              'repeated_corrections': sorted(corrections, key=lambda p: (-p['count'], p['board'], p['signature']))[:MAX_ITEMS],
              'github': {'status': 'disabled'}}
    if kind == 'improvement' and github is not False:
        try:
            bundle['github'] = (github or fetch_github)(repo or DEFAULT_REPO, now)
        except Exception:
            bundle['github'] = {'status': 'unavailable'}
    reasons = []
    unreadable = [b['board'] for b in boards_info if b['status'] == 'unavailable']
    if unreadable:
        reasons.append('board unreadable')  # fail open
    if kind == 'outcome':
        if sum(b.get('open', 0) for b in boards_info):
            reasons.append('open cards')
        if undelivered:
            reasons.append('undelivered approvals')
        fired = last_fire.get((mine or 'default', f'{mine}-outcome'))
        if fired is None or now - fired > timedelta(days=HEARTBEAT_DAYS):
            reasons.append('charter review due')
    else:
        if bundle['repeated_patterns']:
            reasons.append('repeated pattern')
        if bundle['repeated_corrections']:
            reasons.append('repeated correction')
        if kind == 'improvement':
            if failing:
                reasons.append('failing cron')
            if bundle['github'].get('ci_failures', 0) >= MIN_REPEAT:
                reasons.append('repeated CI failure')
    bundle['wake_reasons'] = sorted(set(reasons))
    return bundle, bool(reasons)


def render(bundle, wake):
    gate = json.dumps({'wakeAgent': wake})
    if not wake:
        return gate
    return json.dumps(bundle, sort_keys=True, indent=1) + '\n' + gate


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {'--gate': None, '--home': None, '--now': None, '--github': None}
    while argv and argv[0] in opts and len(argv) > 1:
        opts[argv[0]] = argv[1]
        argv = argv[2:]
    stem = Path(sys.argv[0]).stem
    kind = opts['--gate'] or (stem[5:] if stem.startswith('gate-') else 'improvement')
    try:
        if kind not in KINDS or argv:
            raise ValueError('usage')
        home = Path(opts['--home']) if opts['--home'] else default_home()
        now = parse_ts(opts['--now']) if opts['--now'] else None
        github = False if os.environ.get('NIX_EVIDENCE_GITHUB') == '0' else None
        bundle, wake = collect(home, kind, now, github=github, repo=opts['--github'])
        print(render(bundle, wake))
    except Exception as exc:  # fail open: an unreadable source must never silence a loop
        print(json.dumps({'collector_error': type(exc).__name__}))
        print(json.dumps({'wakeAgent': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
