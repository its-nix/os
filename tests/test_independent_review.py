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


def pr(full_name='its-nix/os', base='main', draft=False, head='a' * 40):
    return {'title': 't', 'body': 'b', 'draft': draft, 'base': {'ref': base},
            'head': {'sha': head, 'repo': {'full_name': full_name}}}


def cmp(files):
    return {'files': files}


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
                self.m.collect('its-nix/os', 1, 'b', 'h')
        for kwargs, message in (({'base': 'release'}, 'into main'), ({'draft': True}, 'draft')):
            with patch.object(self.m, 'github', return_value=pr(**kwargs)):
                with self.assertRaisesRegex(SystemExit, message):
                    self.m.collect('its-nix/os', 1, 'b', 'h')

    def test_oversized_or_unreviewable_diffs_fail_closed(self):
        big = [{'filename': 'a.py', 'status': 'modified', 'patch': 'x' * (self.m.MAX_PATCH_CHARS + 1)}]
        with patch.object(self.m, 'github', side_effect=[pr(), cmp(big)]):
            with self.assertRaisesRegex(SystemExit, 'too large'):
                self.m.collect('its-nix/os', 1, 'b', 'h')
        for files in ([{'filename': 'image.png', 'status': 'added'}],
                      [{'filename': 'b.py', 'previous_filename': 'a.py', 'status': 'renamed', 'patch': ''}]):
            with patch.object(self.m, 'github', side_effect=[pr(), cmp(files)]):
                with self.assertRaisesRegex(SystemExit, 'no reviewable'):
                    self.m.collect('its-nix/os', 1, 'b', 'h')
        many = [{'filename': f'f{i}.py', 'status': 'added', 'patch': '+x'} for i in range(self.m.MAX_FILES)]
        with patch.object(self.m, 'github', side_effect=[pr(), cmp(many)]):
            with self.assertRaisesRegex(SystemExit, 'too large'):
                self.m.collect('its-nix/os', 1, 'b', 'h')

    def run_main(self, result, stop='end_turn', head_after='a' * 40):
        client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(
            create=lambda **kw: Response(result, stop))))
        self.m.anthropic.Anthropic = lambda: client
        files = [{'filename': 'a.py', 'status': 'modified', 'patch': '+x'}]
        env = {'GITHUB_REPOSITORY': 'its-nix/os', 'PR_NUMBER': '7', 'GITHUB_TOKEN': 't',
               'BASE_SHA': 'b' * 40, 'HEAD_SHA': 'a' * 40}
        with patch.dict('os.environ', env), \
                patch.object(self.m, 'github', side_effect=[pr(), cmp(files), pr(head=head_after)]):
            return self.m.main()

    def test_approval_passes_only_without_blocking_findings(self):
        ok = {'verdict': 'APPROVE', 'summary': 's', 'findings': []}
        self.assertEqual(0, self.run_main(ok))
        contradictory = {'verdict': 'APPROVE', 'summary': 's',
                         'findings': [{'severity': 'blocking', 'file': 'a.py', 'issue': 'data loss'}]}
        self.assertEqual(1, self.run_main(contradictory))
        self.assertEqual(1, self.run_main({'verdict': 'REQUEST_CHANGES', 'summary': 's', 'findings': []}))

    def test_head_moved_during_review_fails(self):
        with self.assertRaisesRegex(SystemExit, 'head moved'):
            self.run_main({'verdict': 'APPROVE', 'summary': 's', 'findings': []}, head_after='c' * 40)

    def test_incomplete_or_refused_review_fails_closed(self):
        with self.assertRaises(SystemExit):
            self.run_main({'verdict': 'APPROVE', 'summary': 's', 'findings': []}, stop='refusal')

    def test_workflow_never_executes_pull_request_code(self):
        workflow = (ROOT / '.github/workflows/independent-review.yml').read_text()
        self.assertIn('pull_request_target', workflow)
        checkout = workflow.split('actions/checkout', 1)[1].split('- uses:', 1)[0]
        self.assertIn('base.sha', checkout)
        self.assertNotIn('head.', checkout)  # the PR head is data for the API, never checked out
        self.assertNotIn('head.ref', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('branches: [main]', workflow)
        self.assertNotIn('pull-requests: write', workflow)
        self.assertNotIn('draft == false', workflow)  # a skipped required check would count as passing


if __name__ == '__main__':
    unittest.main()
