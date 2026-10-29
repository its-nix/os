import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.monitoring import reconcile as r


class SchedulerTests(unittest.TestCase):
    def jobs(self):
        return r.load_config()

    def test_preview_idempotency(self):
        self.assertEqual(r.preview(self.jobs(), {}), r.preview(self.jobs(), {}))
        self.assertEqual(2, len(r.preview(self.jobs(), {})))

    def test_same_name_drift_refuses(self):
        job = self.jobs()[0]
        with self.assertRaisesRegex(r.Refusal, "drift"):
            r.preview([job], {job["hermes_profile"]: {job["name"]: {"schedule": "1 2 * * *", "deliver": "discord"}}})

    def test_same_basename_wrong_script_directory_refuses(self):
        job = self.jobs()[0]
        actual = {
            "state": "active", "schedule": job["schedule"], "deliver": job["delivery"],
            "monitor_script": "monitor_snapshot.py",
        }
        with self.assertRaisesRegex(r.Refusal, "script/mode drift"):
            r.preview([job], {job["hermes_profile"]: {job["name"]: actual}})

    def test_duplicate_job_name_refuses(self):
        job = self.jobs()[0]
        actual = {"schedule": job["schedule"], "deliver": job["delivery"], "duplicate": True}
        with self.assertRaisesRegex(r.Refusal, "duplicate"):
            r.preview([job], {job["hermes_profile"]: {job["name"]: actual}})

    def test_stable_idempotency_duplicate_detection(self):
        key = r.idempotency_key("stable-event", "recover")
        self.assertEqual(key, r.idempotency_key("stable-event", "recover"))
        tasks = [{"idempotency_key": key}]
        self.assertTrue(r.task_exists(tasks, key))
        self.assertFalse(r.task_exists(tasks, r.idempotency_key("stable-event", "other")))

    def test_pause_exclusion_and_strict_lead_manifest_routing(self):
        manifest = {
            "projects": [{"id": "p", "paused": True}, {"id": "q"}],
            "profiles": [{"id": "lead-q", "kind": "lead"}, {"id": "worker", "kind": "worker"}],
            "routing": {"q": "lead-q", "p": "worker"},
        }
        self.assertIsNone(r.lead_for_project(manifest, "p"))
        self.assertEqual("lead-q", r.lead_for_project(manifest, "q"))
        manifest["routing"]["q"] = "worker"
        self.assertIsNone(r.lead_for_project(manifest, "q"))
        manifest["routing"]["q"] = "undeclared"
        self.assertIsNone(r.lead_for_project(manifest, "q"))

    def test_retry_cap(self):
        policy = json.loads((r.ROOT / "policy.json").read_text())
        self.assertEqual(2, policy["max_fix_attempts"])
        self.assertTrue(r.retry_allowed(0, policy))
        self.assertTrue(r.retry_allowed(1, policy))
        self.assertFalse(r.retry_allowed(2, policy))
        self.assertFalse(r.retry_allowed(3, policy))
        self.assertFalse(r.retry_allowed(True, policy))

    def test_apply_uses_explicit_monitor_mode_without_live_state(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            calls = []

            def fake(args, **kwargs):
                calls.append(args)
                if args[1:3] == ["config", "get"]:
                    value = {"mode": "manual", "cron_mode": "deny"} if args[3] == "approvals" else True
                    return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
                return subprocess.CompletedProcess(args, 0, "created", "")

            outcomes = r.apply_missing(self.jobs(), {}, hermes="fake-hermes", scripts_root=Path(tmp) / "scripts", runner=fake)
            self.assertEqual(2, len(outcomes))
            creates = [args for args in calls if args[1:3] == ["cron", "create"]]
            self.assertEqual(2, len(creates))
            self.assertIn("--monitor-script", creates[0])
            self.assertNotIn("--script", creates[0])
            self.assertIn("--script", creates[1])
            self.assertNotIn("--monitor-script", creates[1])
            self.assertTrue(all("--continuity" in args for args in creates))
            self.assertEqual("0 */12 * * *", creates[0][-2])
            self.assertEqual("nix-monitoring/monitor_snapshot.py", creates[0][creates[0].index("--monitor-script") + 1])
            self.assertEqual("nix-monitoring/executive_snapshot.py", creates[1][creates[1].index("--script") + 1])
            self.assertTrue((Path(tmp) / "scripts" / "nix-monitoring" / "monitor_snapshot.py").is_file())
            self.assertTrue((Path(tmp) / "scripts" / "nix-monitoring" / "executive_snapshot.py").is_file())
            self.assertTrue((Path(tmp) / "scripts" / "nix-monitoring" / "recovery.py").is_file())

    def test_apply_refuses_unsafe_manifest_and_controls_before_write(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            path = Path(tmp) / "jobs.json"
            data = json.loads((r.ROOT / "jobs.json").read_text())
            data["jobs"][0]["delivery"] = "discord:evil"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(r.Refusal, "reviewed safe shape"):
                r.load_config(path)

            calls = []
            def fake(args, **kwargs):
                calls.append(args)
                value = {"mode": "auto", "cron_mode": "allow"} if args[3] == "approvals" else True
                return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
            scripts = Path(tmp) / "scripts"
            with self.assertRaisesRegex(r.Refusal, "manual approval"):
                r.apply_missing(self.jobs(), {}, hermes="fake-hermes", scripts_root=scripts, runner=fake)
            self.assertEqual(1, len(calls))
            self.assertFalse(scripts.exists())

    def test_runtime_install_rejects_symlink_directory_and_hardlink_file(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            linked = root / "linked"
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(r.Refusal, "symlink"):
                r.install_runtime_scripts(self.jobs(), linked)
            self.assertFalse((outside / "nix-monitoring").exists())

            scripts = root / "scripts"
            dest = scripts / "nix-monitoring"
            dest.mkdir(parents=True, mode=0o700)
            script = self.jobs()[0]["script_source"]
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"do not replace")
            sentinel.chmod(0o700)
            os.link(sentinel, dest / script.name)
            with self.assertRaisesRegex(r.Refusal, "unsafe type, owner, mode, or size"):
                r.install_runtime_scripts(self.jobs(), scripts)
            self.assertEqual(b"do not replace", sentinel.read_bytes())


    def test_existing_jobs_match_exact_cli_shape(self):
        jobs = self.jobs()
        actual = {}
        for job in jobs:
            source_label = "Monitor" if job["execution_mode"] == "monitor" else "Script"
            source_value = f"nix-monitoring/{job['script_file']} (agent runs only on output change)" if source_label == "Monitor" else f"nix-monitoring/{job['script_file']}"
            text = (
                f"  abcdef123456 [active]\n    Name: {job['name']}\n    Schedule: {job['schedule']}\n"
                f"    Deliver: {job['delivery']}\n    {source_label}: {source_value}\n"
            )
            actual[job["name"]] = r.parse_list(text)[job["name"]]
        lines = r.preview(jobs, {"default": actual})
        self.assertEqual(2, len(lines))
        self.assertTrue(all("EXISTS (not modified)" in line for line in lines))

    def test_paused_same_name_refuses_without_resuming(self):
        job = self.jobs()[0]
        actual = {
            "state": "paused",
            "schedule": job["schedule"],
            "deliver": job["delivery"],
            "monitor_script": "monitor_snapshot.py",
        }
        with self.assertRaisesRegex(r.Refusal, "paused/disabled"):
            r.preview([job], {job["hermes_profile"]: {job["name"]: actual}})

    def test_parser_duplicate_names_detected(self):
        listing = """  abcdef123456 [active]\n    Name: same\n    Schedule: 0 1 * * *\n    Deliver: discord\n  abcdef654321 [active]\n    Name: same\n    Schedule: 0 1 * * *\n    Deliver: discord\n"""
        self.assertTrue(r.parse_list(listing)["same"]["duplicate"])


if __name__ == "__main__":
    unittest.main()
