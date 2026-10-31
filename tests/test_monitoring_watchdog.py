import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.monitoring import watchdog as watchdog_module
from automation.monitoring.watchdog import (
    MONITOR_MAX_AGE_SECONDS,
    SCHEDULER_MAX_AGE_SECONDS,
    inspect,
    monitor_job_age,
    send_deduplicated,
)

HEALTHY_CRON = "Scheduler host: the host gateway (PID 2)\n✓ Gateway is running\nTicker heartbeat: 28s ago\n"


class WatchdogTests(unittest.TestCase):
    def monitor_listing(self, now, age=0, status="ok"):
        from datetime import datetime, timezone
        last = datetime.fromtimestamp(now - age, timezone.utc).isoformat()
        return f"  abcdef123456 [active]\n    Name: nix-portfolio-monitor-12h\n    Last run: {last}  {status}\n"

    def runner(self, cron=HEALTHY_CRON, service="active\n", service_code=0, cron_code=0, monitor_list=""):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["systemctl", "--user", "is-active"]:
                return subprocess.CompletedProcess(args, service_code, service, "")
            if args[:3] == ["hermes", "cron", "status"]:
                return subprocess.CompletedProcess(args, cron_code, cron, "")
            if args[:3] == ["hermes", "cron", "list"]:
                return subprocess.CompletedProcess(args, 0, monitor_list, "")
            return subprocess.CompletedProcess(args, 1, "", "unexpected command")
        return run, calls

    def test_checks_gateway_scheduler_and_12h_monitor_heartbeat(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            monitor = Path(tmp) / "monitor"
            monitor.touch()
            now = monitor.stat().st_mtime
            run, calls = self.runner(monitor_list=self.monitor_listing(now, age=5))
            self.assertEqual(inspect("hermes-gateway.service", monitor, now, run), [])
            self.assertEqual(calls, [
                ["systemctl", "--user", "is-active", "hermes-gateway.service"],
                ["hermes", "cron", "status"],
                ["hermes", "cron", "list", "--all"],
            ])
            self.assertGreater(MONITOR_MAX_AGE_SECONDS, 12 * 3600)
            self.assertLessEqual(SCHEDULER_MAX_AGE_SECONDS, 120)

    def test_detects_inactive_gateway_stale_scheduler_and_monitor(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            monitor = Path(tmp) / "monitor"
            monitor.touch()
            now = monitor.stat().st_mtime + MONITOR_MAX_AGE_SECONDS + 1
            stale_cron = "Scheduler host: gateway\nGateway is running\nTicker heartbeat: 121s ago"
            listing = self.monitor_listing(now, age=MONITOR_MAX_AGE_SECONDS + 5)
            run, _ = self.runner(cron=stale_cron, service="inactive\n", service_code=3, monitor_list=listing)
            self.assertEqual(
                inspect("hermes-gateway.service", monitor, now, run),
                ["gateway service is not active", "scheduler heartbeat stale", "monitor job stale", "monitor-cycle heartbeat missing or stale"],
            )

    def test_unrecognized_scheduler_is_unknown_not_healthy(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            monitor = Path(tmp) / "monitor"
            monitor.touch()
            run, _ = self.runner(cron="scheduler maybe fine")
            self.assertIn("scheduler heartbeat unavailable", inspect("hermes-gateway.service", monitor, monitor.stat().st_mtime, run))

    def test_symlink_monitor_heartbeat_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.touch()
            link = root / "link"
            link.symlink_to(target)
            run, _ = self.runner()
            self.assertIn("monitor-cycle heartbeat missing or stale", inspect("hermes-gateway.service", link, target.stat().st_mtime, run))

    def test_healthy_quiet_dedupe_realert_and_failed_send_retry(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            state = Path(tmp) / "state.json"
            invocations = []

            def fake(args, **kwargs):
                invocations.append(args)
                return subprocess.CompletedProcess(args, 0, "sent", "")

            self.assertFalse(send_deduplicated([], state, fake))
            issues = ["gateway service is not active"]
            self.assertTrue(send_deduplicated(issues, state, fake))
            self.assertFalse(send_deduplicated(issues, state, fake))
            self.assertEqual(len(invocations), 1)
            self.assertEqual(invocations[0][:3], ["gws", "gmail", "+send"])
            self.assertIn("--from", invocations[0])
            self.assertIn("lior3226@gmail.com", invocations[0])
            self.assertFalse(send_deduplicated([], state, fake))
            self.assertTrue(send_deduplicated(issues, state, fake))
            self.assertEqual(len(invocations), 2)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(state.parent.stat().st_mode), 0o700)

            fail = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "failure")
            state.unlink()
            self.assertFalse(send_deduplicated(issues, state, fail))
            self.assertFalse(state.exists())

    def test_symlink_lock_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("preserve")
            target.chmod(0o600)
            state = root / "state.json"
            (root / "state.json.lock").symlink_to(target)
            with self.assertRaises(ValueError):
                send_deduplicated(["gateway service is not active"], state, lambda *a, **k: self.fail("email must not be attempted"))
            self.assertEqual("preserve", target.read_text())

    def test_hardlink_lock_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("preserve")
            target.chmod(0o600)
            os.link(target, root / "state.json.lock")
            state = root / "state.json"
            with self.assertRaisesRegex(ValueError, "single-link"):
                send_deduplicated(["gateway service is not active"], state, lambda *a, **k: self.fail("email must not be attempted"))
            self.assertEqual("preserve", target.read_text())

    def test_email_body_is_allowlisted_and_contains_no_source_data(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            state = Path(tmp) / "deep" / "state.json"
            seen = []

            def fake(args, **kwargs):
                seen.extend(args)
                return subprocess.CompletedProcess(args, 0, "", "")

            send_deduplicated(["monitor-cycle heartbeat missing or stale"], state, fake)
            body = seen[seen.index("--body") + 1]
            self.assertEqual(body, "Critical health checks failed:\n- monitor-cycle heartbeat missing or stale\n")
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

    def test_symlink_state_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("{}")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                send_deduplicated([], link)

    def test_state_replacement_race_is_rejected_without_following_symlink(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            state = root / "state.json"
            state.write_text('{"fingerprint":"old"}\\n')
            state.chmod(0o600)
            target = root / "target"
            target.write_text('{"fingerprint":"outside"}\\n')
            target.chmod(0o600)
            original = watchdog_module._open_lock_file

            def replace_after_preflight(lock_path):
                state.unlink()
                state.symlink_to(target)
                return original(lock_path)

            with patch.object(watchdog_module, "_open_lock_file", side_effect=replace_after_preflight):
                with self.assertRaisesRegex(ValueError, "state file is unsafe"):
                    send_deduplicated(["gateway service is not active"], state,
                                      lambda *a, **k: self.fail("email must not be attempted"))
            self.assertEqual('{"fingerprint":"outside"}\\n', target.read_text())

    def test_hardlink_state_rejected_without_modifying_target(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text('{"fingerprint":"preserve"}\\n')
            target.chmod(0o600)
            state = root / "state.json"
            os.link(target, state)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                send_deduplicated(["gateway service is not active"], state,
                                  lambda *a, **k: self.fail("email must not be attempted"))
            self.assertEqual('{"fingerprint":"preserve"}\\n', target.read_text())

    def test_symlink_state_parent_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                send_deduplicated([], alias / "state.json")


if __name__ == "__main__":
    unittest.main()
