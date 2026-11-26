#!/usr/bin/env python3
"""Fixed evaluation of the company-health check against malformed state (acceptance F evaluator).

Usage: python3 tests/health_eval.py --target skills/company-ops/scripts/health.py [--json]

The target is any copy of health.py, so the same fixtures score the repository file, a revert branch
or the bytes of the deployed copy. The scorer is a protected path: a change to it needs founder
approval, so a PR that improves health.py cannot also change the metric that judges it.

Metric: cases handled / total cases in tests/fixtures/health-eval/cases.json. A case is handled when
check() completes, findings match expectations one-to-one, and no malformed file marker appears in
findings, stdout or stderr. Python 3.11 compatible; standard library only.
"""

from datetime import datetime
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
CASES = HERE / 'fixtures' / 'health-eval' / 'cases.json'
CASE_TIMEOUT = 5


def materialise(home, files):
    for relative, content in files.items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            target.write_bytes(bytes.fromhex(content['hex']))
        else:
            target.write_text(content)


def matches(line, group):
    """True when every token appears in the line, case-insensitively. A token a|b accepts either."""
    lowered = line.lower()
    return all(any(option.lower() in lowered for option in token.split('|')) for token in group)


def worker():
    """Run one check with fixture inputs. Findings travel separately from stdout and stderr."""
    inputs = json.load(sys.stdin)
    home = Path.cwd()
    forbidden_read = False

    def kanban(board):
        nonlocal forbidden_read
        state = inputs.get('boards', {}).get(board, [])
        if state == 'forbidden':
            forbidden_read = True
            return []
        if state == 'error':
            raise RuntimeError('timeout')
        return state

    def block_external_calls(event, args):
        # Prevent accidental live probes through Python networking or external commands.
        if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
            raise PermissionError('network and external commands are disabled in the health evaluator')

    sys.addaudithook(block_external_calls)
    now = datetime.fromisoformat(inputs['clock'])
    target = runpy.run_path(str(home.parent / 'health.py'))
    problems = target['check'](home, now, kanban=kanban,
                               t3_ok=inputs.get('t3_ok', True), disk=inputs.get('disk', 0.5))
    result = {'problems': problems, 'forbidden_read': forbidden_read}
    (home.parent / 'result.json').write_text(json.dumps(result), encoding='utf-8')


def match_findings(problems, expected):
    if len({line.strip().lower() for line in problems}) != len(problems):
        return False, 'unexpected finding'
    candidates = [[idx for idx, line in enumerate(problems) if matches(line, group)] for group in expected]
    assigned = {}

    def assign(group_idx, seen):
        # Reassign a line if another group can use an alternative.
        for line_idx in candidates[group_idx]:
            if line_idx in seen:
                continue
            seen.add(line_idx)
            if line_idx not in assigned or assign(assigned[line_idx], seen):
                assigned[line_idx] = group_idx
                return True
        return False

    for group_idx, group in enumerate(expected):
        if not assign(group_idx, set()):
            return False, 'missing finding: ' + ' + '.join(group)
    if len(assigned) != len(problems):
        return False, 'unexpected finding'
    return True, ''


def run_case(target, case, clock):
    markers = [marker.encode() for marker in case.get('forbid', [])]
    inputs = {key: case[key] for key in ('boards', 't3_ok', 'disk') if key in case}
    inputs['clock'] = clock
    with tempfile.TemporaryDirectory(prefix='health-eval-') as directory:
        root = Path(directory).resolve()
        home = root / 'state'
        home.mkdir()
        (root / 'health.py').write_bytes(target)
        materialise(home, case['files'])
        try:
            child = subprocess.run(
                [sys.executable, '-I', '-B', '-S', '-X', 'utf8', str(HERE / 'health_eval.py'), '--worker'],
                cwd=home, env={key: str(home) for key in ('HOME', 'HERMES_HOME', 'TMPDIR')},
                input=json.dumps(inputs).encode(), capture_output=True, timeout=CASE_TIMEOUT,
            )
        except subprocess.TimeoutExpired as error:
            if any(marker in stream for marker in markers for stream in (error.stdout or b'', error.stderr or b'')):
                return False, 'echoed malformed file content'
            return False, 'target timed out'
        if any(marker in stream for marker in markers for stream in (child.stdout, child.stderr)):
            return False, 'echoed malformed file content'
        if child.returncode:
            return False, 'target subprocess failed'
        try:
            result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False, 'target did not produce a result'
    if not isinstance(result, dict):
        return False, 'invalid result'
    if result.get('forbidden_read'):
        return False, 'read a board it must not read'
    problems = result.get('problems')
    if not isinstance(problems, list) or any(not isinstance(line, str) for line in problems):
        return False, 'invalid findings'
    if any(marker in line for line in problems for marker in case.get('forbid', [])):
        return False, 'echoed malformed file content'
    if case.get('silent'):
        return (problems == [], 'expected silence' if problems else '')
    return match_findings(problems, case['expect'])


def version_of(target):
    text = Path(target).read_text()
    found = re.search(r'^VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', text, re.M)
    if found:
        return found.group(1)
    skill = Path(target).resolve().parent.parent / 'SKILL.md'
    if skill.is_file():
        head = skill.read_text().split('---')
        found = re.search(r'^version:\s*["\']?([^"\'\n]+)', head[1], re.M) if len(head) > 2 else None
        if found:
            return found.group(1).strip()
    return 'unversioned'


def evaluate(target, cases_path=CASES):
    fixture_bytes = Path(cases_path).read_bytes()
    fixture = json.loads(fixture_bytes)
    target_bytes = Path(target).read_bytes()
    results = {}
    for case in fixture['cases']:
        ok, why = run_case(target_bytes, case, fixture['clock'])
        results[case['id']] = {'handled': ok, 'why': why, 'kind': case['kind']}
    handled = [i for i, r in results.items() if r['handled']]
    return {
        'target_sha256': hashlib.sha256(target_bytes).hexdigest(),
        'target_version': version_of(target),
        'cases_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'handled': len(handled), 'total': len(results), 'score': f'{len(handled)}/{len(results)}',
        'by_kind': {kind: f'{sum(1 for r in results.values() if r["kind"] == kind and r["handled"])}/'
                          f'{sum(1 for r in results.values() if r["kind"] == kind)}'
                    for kind in sorted({r['kind'] for r in results.values()})},
        'not_handled': sorted(set(results) - set(handled)),
        'results': results,
    }


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        worker()
        sys.exit(0)
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--target', required=True, help='path to a health.py to score')
    parser.add_argument('--json', action='store_true', help='print the full JSON report')
    args = parser.parse_args()
    report = evaluate(args.target)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f'health-eval {report["score"]} (controls {report["by_kind"].get("control")}, '
              f'malformed {report["by_kind"].get("malformed")}) version={report["target_version"]} '
              f'target_sha256={report["target_sha256"][:12]} cases_sha256={report["cases_sha256"][:12]}')
        for case_id in report['not_handled']:
            print(f'  not handled: {case_id}: {report["results"][case_id]["why"]}')
    sys.exit(0)
