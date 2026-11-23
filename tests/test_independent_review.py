import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load():
    sys.modules.setdefault('anthropic', types.ModuleType('anthropic'))  # stubbed; never called for real
    spec = importlib.util.spec_from_file_location('review', ROOT / '.github/review/independent_review.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pr(full_name='its-nix/os'):
    return {'title': 't', 'body': 'b', 'head': {'sha': 'a' * 40, 'repo': {'full_name': full_name}}}


class Block:
    def __init__(self, text):
        self.type, self.text = 'text', text


class Response:
    def __init__(self, result, stop='end_turn'):
        self.stop_reason, self.model = stop, 'claude-opus-5-5'
        self.content = [Block(json.dumps(result))]


class IndependentReviewTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_fork_pull_requests_are_refused(self):
        with patch.object(self.m, 'github', return_value=pr('attacker/os')):
            with self.assertRaisesRegex(SystemExit, 'forks'):
                self.m.collect('its-nix/os', 1)

    def test_oversized_or_unreviewable_diffs_fail_closed(self):
        big = [{'filename': 'a.py', 'status': 'modified', 'patch': 'x' * (self.m.MAX_PATCH_CHARS + 1)}]
        with patch.object(self.m, 'github', side_effect=[pr(), big]):
            with self.assertRaisesRegex(SystemExit, 'too large'):
                self.m.collect('its-nix/os', 1)
        binary = [{'filename': 'image.png', 'status': 'added'}]
        with patch.object(self.m, 'github', side_effect=[pr(), binary]):
            with self.assertRaisesRegex(SystemExit, 'no reviewable'):
                self.m.collect('its-nix/os', 1)

    def run_main(self, result, stop='end_turn'):
        client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **kw: Response(result, stop))))
        self.m.anthropic.Anthropic = lambda: client
        files = [{'filename': 'a.py', 'status': 'modified', 'patch': '+x'}]
        env = {'GITHUB_REPOSITORY': 'its-nix/os', 'PR_NUMBER': '7', 'GITHUB_TOKEN': 't'}
        with patch.dict('os.environ', env), patch.object(self.m, 'github', side_effect=[pr(), files, {}]):
            return self.m.main()

    def test_approval_passes_only_without_blocking_findings(self):
        ok = {'verdict': 'APPROVE', 'summary': 's', 'findings': []}
        self.assertEqual(0, self.run_main(ok))
        contradictory = {'verdict': 'APPROVE', 'summary': 's',
                         'findings': [{'severity': 'blocking', 'file': 'a.py', 'issue': 'data loss'}]}
        self.assertEqual(1, self.run_main(contradictory))
        self.assertEqual(1, self.run_main({'verdict': 'REQUEST_CHANGES', 'summary': 's', 'findings': []}))

    def test_incomplete_or_refused_review_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.run_main({'verdict': 'APPROVE', 'summary': 's', 'findings': []}, stop='refusal')

    def test_workflow_never_executes_pull_request_code(self):
        workflow = (ROOT / '.github/workflows/independent-review.yml').read_text()
        self.assertIn('pull_request_target', workflow)
        self.assertIn('ref: ${{ github.event.pull_request.base.sha }}', workflow)
        self.assertNotIn('head.sha', workflow)
        self.assertNotIn('head.ref', workflow)
        self.assertIn('persist-credentials: false', workflow)


if __name__ == '__main__':
    unittest.main()
