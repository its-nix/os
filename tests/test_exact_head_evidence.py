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
                            ({"total_count": 1, "check_runs": [{k:v for k,v in runrow().items() if k!="id"}]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(status="made_up")]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(status=[])]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(status={})]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(conclusion=[])]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(conclusion={})]}, 0),
                            ({"total_count": 1, "check_runs": [runrow(status="completed", conclusion=None)]}, 0),
                            ({"total_count": 2, "check_runs": [runrow(), runrow()]}, 0),
                            ({}, 1)]:
            with self.subTest(payload=payload, rc=rc):
                runtime, _ = self.capture(payload, rc)
                self.assertEqual(runtime["repo_status"]["base"], "partial")
                self.assertNotEqual(runtime["repo_collection"]["base"]["exact_head_evidence"][0]["status"], "available")

    def test_aggregate_three_repo_full_path_is_bounded(self):
        repos={f"r{i}":f"owner/r{i}" for i in range(3)}
        prs=json.dumps([{"number":n,"state":"OPEN","headRefOid":SHA,"statusCheckRollup":[]} for n in range(1,101)])
        runs={"total_count":100,"check_runs":[runrow(id=n) for n in range(1,101)]}
        calls=[]
        def fake(args, **kwargs):
            calls.append(tuple(args))
            if args[:3]==["gh","pr","list"]: return subprocess.CompletedProcess(args,0,prs,"")
            if args[:2]==["gh","api"]: return subprocess.CompletedProcess(args,0,json.dumps(runs),"")
            if args[:4]==["hermes","kanban","boards","list"]: return subprocess.CompletedProcess(args,0,"[]","")
            return subprocess.CompletedProcess(args,0,"","")
        with patch.object(collector,"load_json",return_value={"repos":repos}), patch.object(collector.subprocess,"run",side_effect=fake):
            runtime=collector.run_readonly_live()
            # Hostile oversized collector output must enter before the sanitizer.
            runtime["repo_collection"]["r0"]["exact_head_evidence"].append(
                {"pr_number":999,"head_sha":SHA,"status":"available",
                 "checks":[runrow(id=n) for n in range(2000)]})
            manifest={"projects":[{"id":r} for r in repos],"profiles":[],"metrics":[]}
            report=collector.discover(manifest,runtime)
        snapshot=monitor_snapshot.compact(report)
        encoded=json.dumps(snapshot,separators=(",",":")).encode()
        self.assertLessEqual(len(encoded),98304)
        self.assertLessEqual(sum(1 for c in calls if len(c)>1 and c[1]=="api"),100)
        self.assertLess(sum(1 for c in calls if len(c)>1 and c[1]=="api"),100)
        self.assertGreater(sum(1 for c in calls if len(c)>1 and c[1]=="api"),0)
        self.assertLessEqual(sum(1 for c in calls if len(c)>1 and c[1]=="pr"),3)
        self.assertTrue(all(runtime["repo_collection"][r]["exact_head_cap_reached"] for r in repos))
        self.assertTrue(any(v in {"partial","unknown"} for v in runtime["repo_status"].values()))
        self.assertTrue(any(v in {"partial","unknown"} for v in report["repo_status"].values()))

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

    def test_sanitizer_rejects_unhashable_exact_evidence_fields(self):
        manifest = {"projects": [{"id": "base"}], "profiles": [], "metrics": []}
        malformed = [
            ({"status": [], "conclusion": "success"}, "available"),
            ({"status": "completed", "conclusion": {}}, "available"),
            ({"status": "completed", "conclusion": "success"}, []),
            ({"status": "completed", "conclusion": "success"}, {}),
        ]
        for check_updates, row_status in malformed:
            runtime = {
                "repo_status": {"base": "available"},
                "repo_collection": {"base": {"exact_head_evidence": [{
                    "pr_number": 7, "head_sha": SHA, "status": row_status,
                    "checks": [{"check_run_id": 1, "status": "completed",
                                "conclusion": "success", "started_at": STAMP,
                                "completed_at": STAMP, **check_updates}],
                }]}}
            }
            with patch.object(collector, "load_json", return_value={"repos": {"base": "owner/repo"}}):
                report = collector.discover(manifest, runtime)
            self.assertIn(report["repo_status"]["base"], {"partial", "unknown"})
            evidence = report["repo_collection"]["base"]["exact_head_evidence"]
            self.assertTrue(evidence[0]["status"] in {"unknown", "partial"})
            if isinstance(check_updates["status"], str) and isinstance(check_updates["conclusion"], str):
                self.assertEqual(len(evidence[0]["checks"]), 1)
            else:
                self.assertEqual(evidence[0]["checks"], [])

    def test_started_at_null_only_for_not_yet_started_status(self):
        runtime, _ = self.capture({"total_count": 1, "check_runs": [runrow(
            status="queued", conclusion=None, started_at=None, completed_at=None)]})
        check = runtime["repo_collection"]["base"]["exact_head_evidence"][0]["checks"][0]
        self.assertIsNone(check["started_at"])
        self.assertEqual(check["conclusion"], "unknown")


if __name__ == "__main__":
    unittest.main()
