"""Guards for the health-check evaluation (tests/health_eval.py). Passes before and after an improvement.

It asserts a floor, never equality: the improvement PR raises the score and must keep this green."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import health_eval  # noqa: E402

HEALTH = ROOT / 'skills/company-ops/scripts/health.py'
FIXTURES = ROOT / 'tests/fixtures/health-eval'


class HealthEvaluationTests(unittest.TestCase):
    def cases(self):
        return json.loads((FIXTURES / 'cases.json').read_text())['cases']

    def test_cases_are_well_formed(self):
        cases = self.cases()
        ids = [case['id'] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({'control', 'malformed'} <= {case['kind'] for case in cases})
        for case in cases:
            self.assertIn(case['kind'], {'control', 'malformed'})
            self.assertTrue(case.get('silent') or case.get('expect'), case['id'])
            self.assertTrue(all(isinstance(group, list) and group for group in case.get('expect', [])), case['id'])

    def test_current_health_check_never_scores_below_the_recorded_floor(self):
        baseline = json.loads((FIXTURES / 'baseline.json').read_text())
        report = health_eval.evaluate(HEALTH)
        handled = {case_id for case_id, result in report['results'].items() if result['handled']}
        self.assertEqual(set(), set(baseline['passing']) - handled, 'a case that was handled before is no longer handled')

    def test_a_check_that_crashes_scores_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'health.py'
            stub.write_text('def check(*args, **kwargs):\n    raise RuntimeError("boom")\n')
            self.assertEqual(0, health_eval.evaluate(stub)['handled'])

    def test_silencing_everything_only_earns_the_silent_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'health.py'
            stub.write_text('def check(*args, **kwargs):\n    return []\n')
            report = health_eval.evaluate(stub)
        silent = {case['id'] for case in self.cases() if case.get('silent')}
        self.assertEqual(silent, {case_id for case_id, result in report['results'].items() if result['handled']})

    def test_reporting_everything_is_an_unexpected_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'health.py'
            stub.write_text('def check(*args, **kwargs):\n    return ["backup stale", "T3 Code unreachable", "health.json unreadable"]\n')
            report = health_eval.evaluate(stub)
        self.assertLess(report['handled'], report['total'] // 2)

    def score_stub(self, source, **fixture):
        case = {'id': 'stub', 'kind': 'malformed', 'files': {}, **fixture}
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'health.py'
            stub.write_text(source)
            cases = Path(directory) / 'cases.json'
            cases.write_text(json.dumps({'clock': '2026-11-24T08:00:00+00:00', 'cases': [case]}))
            return health_eval.evaluate(stub, cases)['results']['stub']

    def test_one_line_cannot_satisfy_two_expected_findings(self):
        result = self.score_stub('def check(*args, **kwargs): return ["backup stale", "disk full"]',
                                 expect=[['backup'], ['stale']])
        self.assertFalse(result['handled'])
        self.assertIn('missing finding', result['why'])

    def test_duplicate_findings_are_unexpected(self):
        result = self.score_stub('def check(*args, **kwargs): return ["backup stale", "backup stale"]',
                                 expect=[['backup'], ['stale']])
        self.assertFalse(result['handled'])
        self.assertEqual('unexpected finding', result['why'])

    def test_overlapping_groups_match_distinct_lines_in_any_order(self):
        result = self.score_stub('def check(*args, **kwargs): return ["backup stale", "backup failed"]',
                                 expect=[['backup'], ['stale']])
        self.assertTrue(result['handled'])

    def test_leaks_in_findings_or_either_stream_fail_without_echo(self):
        sources = (
            'def check(*args, **kwargs): return ["expected marker"]',
            'print("marker")\ndef check(*args, **kwargs): return ["expected"]',
            'import sys\ndef check(*args, **kwargs):\n    print("marker", file=sys.stderr)\n    return ["expected"]',
        )
        for source in sources:
            with self.subTest(source=source):
                result = self.score_stub(source, expect=[['expected']], forbid=['marker'])
                self.assertFalse(result['handled'])
                self.assertEqual('echoed malformed file content', result['why'])

    def test_target_runs_in_isolated_fixture_directory(self):
        source = """import os
import socket
import sys
from pathlib import Path

def check(home, *args, **kwargs):
    assert Path.cwd() == home == Path(os.environ['HOME']) == Path(os.environ['HERMES_HOME'])
    assert set(os.environ) <= {'HOME', 'HERMES_HOME', 'TMPDIR', 'LC_CTYPE', '__CF_USER_TEXT_ENCODING'}
    assert sys.flags.isolated
    try:
        socket.socket()
    except PermissionError:
        return []
    raise AssertionError('socket creation was allowed')
"""
        self.assertTrue(self.score_stub(source, silent=True)['handled'])

    def test_a_hung_target_times_out(self):
        with patch.object(health_eval, 'CASE_TIMEOUT', 0.25):
            result = self.score_stub('import time\ntime.sleep(60)', silent=True)
        self.assertFalse(result['handled'])
        self.assertEqual('target timed out', result['why'])


if __name__ == '__main__':
    unittest.main()
