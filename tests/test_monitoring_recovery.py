import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def test_governance_is_dynamic_and_excludes_paused_archived_or_ambiguous_projects():
    manifest = json.loads(json.dumps(SYNTHETIC_MANIFEST))
    assert recovery.governed(manifest, "synthetic") == ("synthetic-board", "synthetic-lead")
    manifest["projects"][0]["paused"] = True
    with pytest.raises(recovery.Refusal):
        recovery.governed(manifest, "synthetic")
    manifest["projects"][0].pop("paused")
    manifest["projects"][0]["archived"] = True
    with pytest.raises(recovery.Refusal):
        recovery.governed(manifest, "synthetic")
    manifest["projects"][0].pop("archived")
    manifest["projects"].append(dict(manifest["projects"][0]))
    with pytest.raises(recovery.Refusal):
        recovery.governed(manifest, "synthetic")
    with pytest.raises(recovery.Refusal):
        recovery.governed(SYNTHETIC_MANIFEST, "unclassified")


def test_prepare_is_stable_lead_owned_and_proposal_only(tmp_path):
    req = request()
    first = execute(tmp_path, req)
    second = execute(tmp_path, req)
    assert first == second
    assert first["result"] == "approval_required"
    assert first["board"] == "synthetic-board"
    assert first["assignee"] == "synthetic-lead"
    assert first["max_retries"] == 2
    assert first["max_fix_attempts"] == 2
    assert first["requires_human_approval"] is True
    assert first["creation_channel"] == "approval_gated_native_kanban"
    assert "at most 2 distinct attempts total" in first["body"]
    assert first["initial_status"] == "running"
    assert first["idempotency_key"] == recovery.stable_key("a" * 64, "repair-worker")
    assert first["marker"] in first["body"]
    # The helper has no subprocess/CLI integration and cannot create a task.
    assert not hasattr(recovery, "subprocess")
    assert "hermes kanban" not in Path(recovery.__file__).read_text(encoding="utf-8")


def test_prepare_fails_closed_for_stale_or_mismatched_source(tmp_path):
    fresh = (datetime.now(timezone.utc) - timedelta(hours=71)).isoformat()
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(plan(source_kind="stale_task"), {"id": "src1", "status": "running", "updated_at": fresh}))
    stale = (datetime.now(timezone.utc) - timedelta(hours=73)).timestamp()
    result = execute(tmp_path, request(plan(source_kind="stale_task"), {"id": "src1", "status": "running", "updated_at": stale}))
    assert result["result"] == "approval_required"
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(evidence={"id": "other", "status": "blocked"}))


def test_worker_diagnostic_requires_one_exact_critical_finding(tmp_path):
    value = plan(source_kind="worker_diagnostic", diagnostic_kind="worker-crash")
    evidence = {"task_id": "src1", "diagnostics": [{"task_id": "src1", "kind": "worker-crash", "severity": "critical"}]}
    assert execute(tmp_path, request(value, evidence))["result"] == "approval_required"
    for diagnostics in ([], evidence["diagnostics"] * 2, [{"task_id": "src1", "kind": "worker-crash", "severity": "warning"}]):
        with pytest.raises(recovery.Refusal):
            execute(tmp_path, request(value, {"task_id": "src1", "diagnostics": diagnostics}))


def test_repo_check_requires_explicit_mapping_and_exact_failure(tmp_path):
    value = plan(source_kind="repo_check", repo="its-nix/synthetic", pr_number=123, check_name="Lint")
    value.pop("source_task_id")
    evidence = {"repo": "its-nix/synthetic", "pr_number": 123, "state": "OPEN",
                "checks": [{"name": "Lint", "conclusion": "FAILURE"}]}
    assert execute(tmp_path, request(value, evidence))["result"] == "approval_required"
    assert value["event_key"] in recovery._marker(value)
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(dict(value, repo="elsewhere/synthetic"), evidence))
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(value, dict(evidence, checks=[{"name": "Lint", "state": "IN_PROGRESS"}])))
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(value, dict(evidence, checks=[{"name": "Lint", "conclusion": "SUCCESS"}])))


def test_independent_readback_verifies_task_and_fresh_source_resolution(tmp_path):
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
    assert execute(tmp_path, req, mode="verify") == {"result": "verified_resolved", "task_id": "rec1", "status": "done"}
    req["verification_evidence"] = {"id": "src1", "status": "blocked"}
    assert execute(tmp_path, req, mode="verify")["result"] == "unresolved"
    req["verification_evidence"] = {"id": "src1", "status": "ready"}
    req["recovery_task"]["status"] = "running"
    assert execute(tmp_path, req, mode="verify")["result"] == "unresolved"


def test_independent_verification_requires_a_fresh_source_observation(tmp_path):
    value = plan()
    req = request(value, {"id": "src1", "status": "ready"}, recovery_task_id="rec1",
                  recovery_task={"id": "rec1", "status": "done", "board_id": "synthetic-board",
                                 "assignee": "synthetic-lead", "max_retries": 2,
                                 "idempotency_key": recovery.stable_key("a" * 64, "repair-worker"),
                                 "body": recovery._marker(value)})
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, req, mode="verify")


@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("board_id", "other-board"), ("assignee", "wrong-lead"),
    ("max_retries", 3), ("max_retries", True), ("idempotency_key", "bad-key"), ("body", "missing marker"),
])
def test_readback_mismatch_refused(tmp_path, field, value):
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
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, req, mode="verify")


def test_invalid_plan_and_unsupported_mode_fail_closed(tmp_path):
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(plan(event_key="not-a-hash")))
    with pytest.raises(recovery.Refusal):
        execute(tmp_path, request(), mode="create")


def test_dynamic_governed_finding_produces_idempotent_lead_handoff_and_fresh_verification(tmp_path):
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
    assert prepared["assignee"] == "synthetic-lead"
    assert prepared["idempotency_key"] == recovery.stable_key(event["key"], "repair-worker")
    assert recovery.prepare(prepare_request, SYNTHETIC_MANIFEST, SYNTHETIC_POLICY) == prepared

    readback = {"id": "rec1", "status": "done", "board_id": "synthetic-board",
                "assignee": "synthetic-lead", "max_retries": 2,
                "idempotency_key": prepared["idempotency_key"], "body": prepared["body"]}
    verify_request = request(plan_value, {"id": "src1", "status": "blocked"},
                             verification_evidence={"id": "src1", "status": "ready"},
                             recovery_task_id="rec1", recovery_task=readback)
    assert recovery.verify(verify_request, SYNTHETIC_MANIFEST, SYNTHETIC_POLICY) == {
        "result": "verified_resolved", "task_id": "rec1", "status": "done"}


def test_stable_key_changes_with_action():
    one = recovery.stable_key("a" * 64, "repair-worker")
    two = recovery.stable_key("a" * 64, "retry-task")
    assert one != two
    assert len(one) == 64 and all(c in "0123456789abcdef" for c in one)
    assert hashlib.sha256(("a" * 64 + "\0repair-worker").encode()).hexdigest() == one
