#!/usr/bin/env python3
"""Fixed evaluation of the company-health check against malformed state (acceptance F evaluator).

Usage: python3 tests/health_eval.py --target skills/company-ops/scripts/health.py [--json]

The target is any copy of health.py, so the same fixtures score the repository file, a revert branch
or the bytes of the deployed copy. The scorer is a protected path: a change to it needs founder
approval, so a PR that improves health.py cannot also change the metric that judges it.

Metric: cases handled / total cases in tests/fixtures/health-eval/cases.json. A case is handled when
check() does not raise, every expected finding is reported, no other finding appears, and no marker
text from a malformed file is echoed. Python 3.11 compatible; standard library only.
"""

from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
CASES = HERE / 'fixtures' / 'health-eval' / 'cases.json'
NOW = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)
sys.dont_write_bytecode = True  # never leave bytecode beside a scored copy of health.py


def load_target(path):
    spec = importlib.util.spec_from_file_location('health_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def run_case(module, case):
    boards = case.get('boards', {})
    forbidden_reads = []

    def kanban(board):
        state = boards.get(board, [])
        if state == 'forbidden':
            forbidden_reads.append(board)
            return []
        if state == 'error':
            raise RuntimeError('timeout')
        return state

    with tempfile.TemporaryDirectory(prefix='health-eval-') as directory:
        home = Path(directory)
        materialise(home, case['files'])
        try:
            problems = module.check(home, NOW, kanban=kanban, t3_ok=case.get('t3_ok', True), disk=case.get('disk', 0.5))
        except Exception as error:  # a crash is exactly what this evaluation measures
            return False, f'raised {type(error).__name__}'
    if forbidden_reads:
        return False, 'read a board it must not read'
    problems = [str(problem) for problem in problems]
    if case.get('silent'):
        return (problems == [], 'expected silence' if problems else '')
    missing = [group for group in case['expect'] if not any(matches(line, group) for line in problems)]
    if missing:
        return False, 'missing finding: ' + ' + '.join(missing[0])
    extra = [line for line in problems if not any(matches(line, group) for group in case['expect'])]
    if extra:
        return False, 'unexpected finding'
    if any(marker in line for line in problems for marker in case.get('forbid', [])):
        return False, 'echoed malformed file content'
    return True, ''


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
    cases = json.loads(Path(cases_path).read_text())['cases']
    module = load_target(target)
    results = {}
    for case in cases:
        ok, why = run_case(module, case)
        results[case['id']] = {'handled': ok, 'why': why, 'kind': case['kind']}
    handled = [i for i, r in results.items() if r['handled']]
    return {
        'target_sha256': hashlib.sha256(Path(target).read_bytes()).hexdigest(),
        'target_version': version_of(target),
        'cases_sha256': hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
        'handled': len(handled), 'total': len(results), 'score': f'{len(handled)}/{len(results)}',
        'by_kind': {kind: f'{sum(1 for r in results.values() if r["kind"] == kind and r["handled"])}/'
                          f'{sum(1 for r in results.values() if r["kind"] == kind)}'
                    for kind in sorted({r['kind'] for r in results.values()})},
        'not_handled': sorted(set(results) - set(handled)),
        'results': results,
    }


if __name__ == '__main__':
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
