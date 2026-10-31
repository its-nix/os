import json
import subprocess
import unittest
from unittest.mock import patch

from automation.monitoring import collector, monitor_snapshot

SHA = "a" * 40
STAMP = "2026-01-02T03:04:05Z"


def runrow(**updates):
    value = {"id": 123, "head_sha": SHA, "status": "completed", "conclusion": "success",
             "started_at": STAMP, "completed_at": STAMP}
    value.update(updates)
    return value


class ExactHeadEvidenceTests(unittest.TestCase):
    def capture(self, api_payload, api_rc=0):
        prs = json.dumps([{"number": 7, "state": "OPEN", "headRefOid": SHA,
                           "statusCheckRollup": []}])
        calls = []
        def fake(args, **kwargs):
            calls.append(tuple(args))
            if args[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(args, 0, prs, "")
            if args[:2] == ["gh", "api"]:
                out = json.dumps(api_payload) if isinstance(api_payload, dict) else str(api_payload)
                return subprocess.CompletedProcess(args, api_rc, out, "secret stderr")
            if args[:4] == ["hermes", "kanban", "boards", "list"]:
                return subprocess.CompletedProcess(args, 0, "[]", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(collector, "load_json", return_value={"repos": {"base": "owner/repo"}}), \
             patch.object(collector.subprocess, "run", side_effect=fake):
            runtime = collector.run_readonly_live()
        return runtime, calls

    def test_rest_failures_mismatch_empty_and_caps_are_not_available(self):
        for payload, rc in [({"total_count": 1, "check_runs": [runrow(head_sha="b" * 40)]}, 0),
                            ({"total_count": 0, "check_runs": []}, 0),
                            ({"total_count": 2, "check_runs": [runrow()]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(started_at="bad")]}, 0),
                            ({}, 1)]:
            with self.subTest(payload=payload, rc=rc):
                runtime, _ = self.capture(payload, rc)
                self.assertEqual(runtime["repo_status"]["base"], "partial")
                self.assertNotEqual(runtime["repo_collection"]["base"]["exact_head_evidence"][0]["status"], "available")

    def test_rest_uses_exact_sha_and_survives_full_path(self):
        runtime, calls = self.capture({"total_count": 1, "check_runs": [runrow()]})
        api = [call for call in calls if len(call) > 1 and call[1] == "api"]
        self.assertEqual(len(api), 1)
        self.assertIn(f"commits/{SHA}/check-runs?per_page=100", api[0][2])
        evidence = runtime["repo_collection"]["base"]["exact_head_evidence"][0]
        self.assertEqual(evidence["status"], "available")
        manifest = {"projects": [{"id": "base"}], "profiles": [], "metrics": []}
        with patch.object(collector, "load_json", return_value={"repos": {"base": "owner/repo"}}):
            report = collector.discover(manifest, runtime)
        compact = monitor_snapshot.compact(report)
        found = compact["repo_collection"]["base"]["exact_head_evidence"][0]
        self.assertEqual(found["head_sha"], SHA)
        self.assertEqual(found["checks"][0]["check_run_id"], 123)
        self.assertEqual(found["status"], "available")
        self.assertNotIn("secret stderr", json.dumps(compact))


if __name__ == "__main__":
    unittest.main()
