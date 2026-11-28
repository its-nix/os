import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'skills/t3-delegate/scripts/launch_guard.py'
SKILL = ROOT / 'skills/t3-delegate/SKILL.md'
PR = 'https://github.com/example/repo/pull/7'

spec = importlib.util.spec_from_file_location('launch_guard', TOOL)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.record_path = self.home / 'ventures/acme/t3/t_card1.json'

    def run_guard(self, *argv, timeout=None):
        """In-process call; returns (exit code, stdout JSON or text, stderr JSON or text)."""
        argv = list(argv)
        if timeout is not None:
            argv = ['--lock-timeout', str(timeout)] + argv
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = guard.main(argv)
            except SystemExit as exit_:
                code = exit_.code
        return code, self.parse(out.getvalue()), self.parse(err.getvalue())

    @staticmethod
    def parse(text):
        try:
            return json.loads(text)
        except ValueError:
            return text

    def ids(self, command, *extra):
        return (command, '--venture', 'acme', '--card', 't_card1', *extra)

    def begin(self, *extra, **kw):
        return self.run_guard(*self.ids('begin', *extra), **kw)

    def launch(self, thread='thr-1', *extra):
        return self.run_guard(*self.ids('launched', '--thread-id', thread, '--provider', 'codex', *extra))

    def stored(self):
        return json.loads(self.record_path.read_text())


class DoubleLaunchTests(GuardCase):
    def test_first_begin_launches_and_second_is_refused_in_every_state(self):
        code, out, _ = self.begin()
        self.assertEqual(0, code)
        self.assertEqual('launch', out['action'])
        self.assertEqual('acme/t_card1', out['branch'])
        before = self.record_path.read_text()

        code, _, err = self.begin()  # intent, no thread yet: retry after a crash before the launch reply
        self.assertEqual(3, code)
        self.assertEqual('search_thread', err['action'])
        self.assertEqual(before, self.record_path.read_text())

        self.launch()
        code, _, err = self.begin()
        self.assertEqual(3, code)
        self.assertEqual(('read_thread', 'thr-1'), (err['action'], err['threadId']))
        self.assertIn('Do not call t3_thread_launch', err['next'])

        self.run_guard(*self.ids('complete', '--pr', PR))
        code, _, err = self.begin()
        self.assertEqual((3, 'completed', PR), (code, err['status'], err['prUrl']))

    def test_other_cards_and_ventures_are_independent(self):
        self.begin()
        self.assertEqual(0, self.run_guard('begin', '--venture', 'acme', '--card', 't_other')[0])
        self.assertEqual(0, self.run_guard('begin', '--venture', 'beta', '--card', 't_card1')[0])

    def test_invalid_ids_are_rejected_without_touching_disk(self):
        for venture, card in (('..', 'c'), ('acme', '../x'), ('acme', 'a/b'), ('', 'c'), ('acme', '.hidden')):
            code, _, err = self.run_guard('begin', '--venture', venture, '--card', card)
            self.assertEqual(2, code, (venture, card))
        self.assertFalse((self.home / 'ventures').exists())


class RecoveryTests(GuardCase):
    def test_recover_needs_reason_and_an_existing_record(self):
        code, _, _ = self.begin('--recover', '--reason', 'x')
        self.assertEqual(4, code)  # nothing to recover
        self.begin()
        self.launch()
        code, _, err = self.begin('--recover')
        self.assertEqual(2, code)
        self.assertIn('--reason', err['error'])
        self.assertEqual('launched', self.stored()['status'])

    def test_recover_archives_old_attempt_and_allows_one_new_launch(self):
        self.begin()
        self.launch()
        code, out, _ = self.begin('--recover', '--reason', 'thread read: harness crashed, no commits')
        self.assertEqual(0, code)
        self.assertEqual((2, 'acme/t_card1-r2'), (out['attempt'], out['branch']))
        data = self.stored()
        self.assertEqual(('intent', None), (data['status'], data['threadId']))
        self.assertEqual('thr-1', data['history'][0]['threadId'])
        self.assertIn('harness crashed', data['history'][0]['reason'])
        self.assertEqual(3, self.begin()[0])  # the recovery launch is guarded too
        self.assertEqual(0, self.launch('thr-2')[0])
        self.assertEqual('thr-2', self.stored()['threadId'])


class RestartTests(GuardCase):
    def test_new_process_reads_existing_record_instead_of_relaunching(self):
        def cli(*argv):
            return subprocess.run([sys.executable, '-I', str(TOOL), *argv], capture_output=True, text=True,
                                  env={**os.environ, 'HERMES_HOME': str(self.home)})
        first = cli(*self.ids('begin'))
        self.assertEqual(0, first.returncode, first.stderr)
        cli(*self.ids('launched', '--thread-id', 'thr-9', '--link', 'http://t3.invalid/thr-9'))
        again = cli(*self.ids('begin'))  # lead restarted and replays the launch step
        self.assertEqual(3, again.returncode)
        self.assertEqual('thr-9', json.loads(again.stderr)['threadId'])
        shown = cli(*self.ids('show'))
        self.assertEqual(0, shown.returncode)
        self.assertEqual(('launched', 'thr-9'), (json.loads(shown.stdout)['status'], json.loads(shown.stdout)['threadId']))

    def test_corrupt_record_is_never_overwritten(self):
        self.record_path.parent.mkdir(parents=True)
        self.record_path.write_text('{not json')
        self.assertEqual(2, self.begin()[0])
        self.assertEqual('{not json', self.record_path.read_text())
        self.assertEqual(2, self.run_guard(*self.ids('show'))[0])

    def test_show_and_receipt_without_record_fail_cleanly(self):
        self.assertEqual(4, self.run_guard(*self.ids('show'))[0])
        self.assertEqual(4, self.run_guard(*self.ids('receipt'))[0])


class ReceiptTests(GuardCase):
    def test_transitions_intent_launched_completed(self):
        self.begin()
        self.assertEqual('intent', self.stored()['status'])
        self.assertEqual(4, self.run_guard(*self.ids('complete', '--pr', PR))[0])  # no thread yet

        self.assertEqual(0, self.launch('thr-1', '--branch', 'acme/t_card1', '--link', 'http://t3.invalid/1')[0])
        data = self.stored()
        self.assertEqual(('launched', 'thr-1', 'codex'), (data['status'], data['threadId'], data['provider']))
        self.assertIn('launchedAt', data)
        self.assertEqual(0, self.launch('thr-1')[0])  # idempotent
        code, _, err = self.launch('thr-2')  # a second thread for the card is a conflict
        self.assertEqual(4, code)
        self.assertEqual('thr-1', err['threadId'])

        code, out, _ = self.run_guard(*self.ids('complete', '--pr', PR, '--ci', 'pending', '--review', 'pending',
                                                '--evidence', 'repro: make check -> exit 0'))
        self.assertEqual((0, 'completed'), (code, out['status']))
        self.assertIn('completedAt', self.stored())

        code, _, _ = self.run_guard(*self.ids('complete', '--pr', PR, '--ci', 'green', '--review', 'approved',
                                              '--merge', 'merged', '--deploy', 'not-applicable'))
        self.assertEqual(0, code)
        receipt = self.stored()['receipt']
        self.assertEqual(('green', 'approved', 'merged', 'not-applicable', 'repro: make check -> exit 0'),
                         tuple(receipt[k] for k in ('ci', 'review', 'merge', 'deploy', 'evidence')))

    def test_receipt_block_lists_every_required_field(self):
        self.begin()
        self.launch('thr-1', '--link', 'http://t3.invalid/1')
        self.run_guard(*self.ids('complete', '--pr', PR, '--ci', 'green', '--evidence', 'before 3 -> after 4'))
        code, text, _ = self.run_guard(*self.ids('receipt'))
        self.assertEqual(0, code)
        for needle in ('card t_card1', 'status completed', 'Thread: thr-1 (http://t3.invalid/1)', 'Provider: codex',
                       'Branch: acme/t_card1', f'PR: {PR}', 'CI: green', 'Review: pending', 'Merge: open',
                       'Deploy: pending', 'Verification evidence: before 3 -> after 4'):
            self.assertIn(needle, text)

    def test_bad_inputs_are_rejected(self):
        self.begin()
        self.launch()
        self.assertEqual(2, self.run_guard(*self.ids('complete', '--pr', 'not-a-url'))[0])
        self.assertEqual(2, self.run_guard(*self.ids('complete', '--pr', PR, '--ci', 'maybe'))[0])
        self.run_guard(*self.ids('complete', '--pr', PR))
        code, _, err = self.run_guard(*self.ids('complete', '--pr', 'https://github.com/example/repo/pull/8'))
        self.assertEqual(4, code)
        self.assertIn('--recover', err['error'])


class LockTests(GuardCase):
    def test_held_lock_times_out_without_writing(self):
        lock = self.home / 'ventures/acme/t3/t_card1.lock'
        lock.parent.mkdir(parents=True)
        with open(lock, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            code, _, err = self.begin(timeout=0.2)
        self.assertEqual(5, code)
        self.assertIn('lock', err['error'])
        self.assertFalse(self.record_path.exists())
        self.assertEqual(0, self.begin(timeout=0.2)[0])  # released: works

    def test_concurrent_begins_yield_exactly_one_launch(self):
        procs = [subprocess.Popen([sys.executable, '-I', str(TOOL), *self.ids('begin')], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env={**os.environ, 'HERMES_HOME': str(self.home)})
                 for _ in range(8)]
        codes = sorted(p.wait(timeout=60) for p in procs)
        for p in procs:
            p.stdout.close(), p.stderr.close()
        self.assertEqual([0] + [3] * 7, codes)
        self.assertEqual('intent', self.stored()['status'])
        self.assertEqual([], [p.name for p in self.record_path.parent.glob('.t3-*.tmp')])


class SkillContractTests(unittest.TestCase):
    def test_skill_wires_the_guard_receipt_and_brief_rules(self):
        text = SKILL.read_text()
        for needle in ('scripts/launch_guard.py begin', 'launch_guard.py launched', 'launch_guard.py complete',
                       'launch_guard.py receipt', '--recover --reason', 'never call `t3_thread_launch`',
                       'Verification oracle', '`test-change`', 'Before/after evidence', 'Expensive to reverse',
                       'migration and rollback sketch', 'No planning documents', 'Expected diff size', 'Task packs',
                       'verification evidence'):
            self.assertIn(needle, text)
        self.assertLess(text.index('Launch guard'), text.index('4. **Launch**'))


if __name__ == '__main__':
    unittest.main()
