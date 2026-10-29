import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.monitoring import recovery


SYNTHETIC_MANIFEST = {
    "projects": [{"id": "synthetic", "board": "synthetic-board", "lead": "synthetic-lead"}],
    "profiles": [{"id": "synthetic-lead", "kind": "lead", "projects": ["synthetic"]}],
}
SYNTHETIC_POLICY = {
    "max_fix_attempts": 2,
    "stale_after_hours": 72,
    "repos": {"synthetic": "its-nix/synthetic"},
}


def plan(**changes):
    value = {
        "project_id": "synthetic",
        "source_task_id": "src1",
        "source_kind": "blocked_task",
        "event_key": "a" * 64,
        "action": "repair-worker",
        "title": "Repair worker",
        "summary": "Investigate the blocked synthetic task.",
        "acceptance_check": "Source task is no longer blocked.",
    }
    value.update(changes)
    return value


def request(value=None, evidence=None, verification_evidence=None, **extra):
    result = {"plan": value or plan(), "source_evidence": evidence or {"id": "src1", "status": "blocked"}}
    if verification_evidence is not None:
        result["verification_evidence"] = verification_evidence
    result.update(extra)
    return result


def execute(tmp_path, req, mode="prepare"):
    manifest_path = tmp_path / "manifest.yaml"
    policy_path = tmp_path / "policy.json"
    manifest_path.write_text(json.dumps(SYNTHETIC_MANIFEST), encoding="utf-8")
    policy_path.write_text(json.dumps(SYNTHETIC_POLICY), encoding="utf-8")
    return recovery.execute(req, mode=mode, manifest_path=manifest_path, policy_path=policy_path)


class RecoveryTests(unittest.TestCase):
    def run_execute(self, req, mode="prepare"):
        with tempfile.TemporaryDirectory() as directory:
            return execute(Path(directory), req, mode)

    def test_governance_is_dynamic_and_excludes_paused_archived_or_ambiguous_projects(self):
        manifest = json.loads(json.dumps(SYNTHETIC_MANIFEST))
        self.assertEqual(recovery.governed(manifest, "synthetic"), ("synthetic-board", "synthetic-lead"))
        manifest["projects"][0]["paused"] = True
        with self.assertRaises(recovery.Refusal):
            recovery.governed(manifest, "synthetic")
        manifest["projects"][0].pop("paused")
        manifest["projects"][0]["archived"] = True
        with self.assertRaises(recovery.Refusal):
            recovery.governed(manifest, "synthetic")
        manifest["projects"][0].pop("archived")
        manifest["projects"].append(dict(manifest["projects"][0]))
        with self.assertRaises(recovery.Refusal):
            recovery.governed(manifest, "synthetic")
        with self.assertRaises(recovery.Refusal):
            recovery.governed(SYNTHETIC_MANIFEST, "unclassified")

    def test_prepare_is_stable_lead_owned_and_proposal_only(self):
        req = request()
        first = self.run_execute(req)
        second = self.run_execute(req)
        self.assertEqual(first, second)
        self.assertEqual(first["result"], "approval_required")
        self.assertEqual(first["board"], "synthetic-board")
        self.assertEqual(first["assignee"], "synthetic-lead")
        self.assertEqual(first["max_retries"], 2)
        self.assertEqual(first["max_fix_attempts"], 2)
        self.assertIs(first["requires_human_approval"], True)
        self.assertEqual(first["creation_channel"], "approval_gated_native_kanban")
        self.assertIn("at most 2 distinct attempts total", first["body"])
        self.assertEqual(first["initial_status"], "running")
        self.assertEqual(first["idempotency_key"], recovery.stable_key("a" * 64, "repair-worker"))
        self.assertIn(first["marker"], first["body"])
        # The helper has no subprocess/CLI integration and cannot create a task.
        self.assertFalse(hasattr(recovery, "subprocess"))
        self.assertNotIn("hermes kanban", Path(recovery.__file__).read_text(encoding="utf-8"))

    def test_prepare_fails_closed_for_stale_or_mismatched_source(self):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=71)).isoformat()
        with self.assertRaises(recovery.Refusal):
            self.run_execute(request(plan(source_kind="stale_task"), {"id": "src1", "status": "running", "updated_at": fresh}))
        stale = (datetime.now(timezone.utc) - timedelta(hours=73)).timestamp()
        result = self.run_execute(request(plan(source_kind="stale_task"), {"id": "src1", "status": "running", "updated_at": stale}))
        self.assertEqual(result["result"], "approval_required")
        with self.assertRaises(recovery.Refusal):
            self.run_execute(request(evidence={"id": "other", "status": "blocked"}))

    def test_worker_diagnostic_requires_one_exact_critical_finding(self):
        value = plan(source_kind="worker_diagnostic", diagnostic_kind="worker-crash")
        evidence = {"task_id": "src1", "diagnostics": [{"task_id": "src1", "kind": "worker-crash", "severity": "critical"}]}
        self.assertEqual(self.run_execute(request(value, evidence))["result"], "approval_required")
        for diagnostics in ([], evidence["diagnostics"] * 2, [{"task_id": "src1", "kind": "worker-crash", "severity": "warning"}]):
            with self.subTest(diagnostics=diagnostics):
                with self.assertRaises(recovery.Refusal):
                    self.run_execute(request(value, {"task_id": "src1", "diagnostics": diagnostics}))

    def test_repo_check_requires_explicit_mapping_and_exact_failure(self):
        value = plan(source_kind="repo_check", repo="its-nix/synthetic", pr_number=123, check_name="Lint")
        value.pop("source_task_id")
        evidence = {"repo": "its-nix/synthetic", "pr_number": 123, "state": "OPEN",
                    "checks": [{"name": "Lint", "conclusion": "FAILURE"}]}
        self.assertEqual(self.run_execute(request(value, evidence))["result"], "approval_required")
        self.assertIn(value["event_key"], recovery._marker(value))
        invalid_requests = (
            request(dict(value, repo="elsewhere/synthetic"), evidence),
            request(value, dict(evidence, checks=[{"name": "Lint", "state": "IN_PROGRESS"}])),
            request(value, dict(evidence, checks=[{"name": "Lint", "conclusion": "SUCCESS"}])),
        )
        for invalid in invalid_requests:
            with self.subTest(invalid=invalid):
                with self.assertRaises(recovery.Refusal):
                    self.run_execute(invalid)

    def test_independent_readback_verifies_task_and_fresh_source_resolution(self):
        value = plan()
        prepared = recovery.prepare(request(value, {"id": "src1", "status": "blocked"}),
                                    SYNTHETIC_MANIFEST, SYNTHETIC_POLICY)
        req = request(value, {"id": "src1", "status": "blocked"},
                      verification_evidence={"id": "src1", "status": "ready"},
                      recovery_task_id="rec1",
                      recovery_task={"id": "rec1", "status": "done", "board_id": "synthetic-board",
                                     "assignee": "synthetic-lead", "max_retries": prepared["max_retries"],
                                     "idempotency_key": prepared["idempotency_key"],
                                     "body": prepared["body"]})
        self.assertEqual(self.run_execute(req, mode="verify"),
                         {"result": "verified_resolved", "task_id": "rec1", "status": "done"})
        req["verification_evidence"] = {"id": "src1", "status": "blocked"}
        self.assertEqual(self.run_execute(req, mode="verify")["result"], "unresolved")
        req["verification_evidence"] = {"id": "src1", "status": "ready"}
        req["recovery_task"]["status"] = "running"
        self.assertEqual(self.run_execute(req, mode="verify")["result"], "unresolved")

    def test_independent_verification_requires_a_fresh_source_observation(self):
        value = plan()
        req = request(value, {"id": "src1", "status": "ready"}, recovery_task_id="rec1",
                      recovery_task={"id": "rec1", "status": "done", "board_id": "synthetic-board",
                                     "assignee": "synthetic-lead", "max_retries": 2,
                                     "idempotency_key": recovery.stable_key("a" * 64, "repair-worker"),
                                     "body": recovery._marker(value)})
        with self.assertRaises(recovery.Refusal):
            self.run_execute(req, mode="verify")

    def test_readback_mismatch_refused(self):
        mismatches = [
            ("id", "other"), ("board_id", "other-board"), ("assignee", "wrong-lead"),
            ("max_retries", 3), ("max_retries", True), ("idempotency_key", "bad-key"),
            ("body", "missing marker"),
        ]
        for field, value in mismatches:
            with self.subTest(field=field, value=value):
                plan_value = plan()
                prepared = recovery.prepare(request(plan_value, {"id": "src1", "status": "blocked"}),
                                            SYNTHETIC_MANIFEST, SYNTHETIC_POLICY)
                readback = {"id": "rec1", "status": "done", "board_id": "synthetic-board",
                            "assignee": "synthetic-lead", "max_retries": prepared["max_retries"],
                            "idempotency_key": prepared["idempotency_key"], "body": prepared["body"]}
                readback[field] = value
                req = request(plan_value, {"id": "src1", "status": "blocked"},
                              verification_evidence={"id": "src1", "status": "ready"},
                              recovery_task_id="rec1", recovery_task=readback)
                with self.assertRaises(recovery.Refusal):
                    self.run_execute(req, mode="verify")

    def test_invalid_plan_and_unsupported_mode_fail_closed(self):
        with self.assertRaises(recovery.Refusal):
            self.run_execute(request(plan(event_key="not-a-hash")))
        with self.assertRaises(recovery.Refusal):
            self.run_execute(request(), mode="create")

    def test_dynamic_governed_finding_produces_idempotent_lead_handoff_and_fresh_verification(self):
        from automation.monitoring.collector import discover, stable_events

        runtime = {
            "boards": [{"id": "synthetic-board"}],
            "profiles": [{"name": "synthetic-lead", "kind": "lead"}],
            "tasks": [{"id": "src1", "board_id": "synthetic-board", "project_id": "synthetic",
                       "status": "blocked", "created_at": "2026-01-01T00:00:00Z"}],
            "board_list_status": "available",
            "task_collection_status": {"synthetic-board": {"status": "available"}},
            "status": {},
            "repos": {},
        }
        report = discover(SYNTHETIC_MANIFEST, runtime,
                          now=datetime(2026, 1, 10, tzinfo=timezone.utc))
        event = next(item for item in stable_events(report) if item.get("task", {}).get("id") == "src1")
        plan_value = plan(event_key=event["key"])
        prepare_request = request(plan_value, {"id": "src1", "status": "blocked"})
        prepared = recovery.prepare(prepare_request, SYNTHETIC_MANIFEST, SYNTHETIC_POLICY)
        self.assertEqual(prepared["assignee"], "synthetic-lead")
        self.assertEqual(prepared["idempotency_key"], recovery.stable_key(event["key"], "repair-worker"))
        self.assertEqual(recovery.prepare(prepare_request, SYNTHETIC_MANIFEST, SYNTHETIC_POLICY), prepared)

        readback = {"id": "rec1", "status": "done", "board_id": "synthetic-board",
                    "assignee": "synthetic-lead", "max_retries": 2,
                    "idempotency_key": prepared["idempotency_key"], "body": prepared["body"]}
        verify_request = request(plan_value, {"id": "src1", "status": "blocked"},
                                 verification_evidence={"id": "src1", "status": "ready"},
                                 recovery_task_id="rec1", recovery_task=readback)
        self.assertEqual(recovery.verify(verify_request, SYNTHETIC_MANIFEST, SYNTHETIC_POLICY),
                         {"result": "verified_resolved", "task_id": "rec1", "status": "done"})

    def test_stable_key_changes_with_action(self):
        one = recovery.stable_key("a" * 64, "repair-worker")
        two = recovery.stable_key("a" * 64, "retry-task")
        self.assertNotEqual(one, two)
        self.assertEqual(len(one), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in one))
        self.assertEqual(hashlib.sha256(("a" * 64 + "\0repair-worker").encode()).hexdigest(), one)


if __name__ == "__main__":
    unittest.main()
