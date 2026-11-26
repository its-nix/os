"""Guards for the health-check evaluation (tests/health_eval.py). Passes before and after an improvement.

It asserts a floor, never equality: the improvement PR raises the score and must keep this green."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
