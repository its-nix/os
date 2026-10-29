#!/usr/bin/env python3
"""Pure, bounded recovery-request planner and readback validator.

This module never invokes Hermes, GitHub, or a subprocess. Scheduled callers may
prepare an approval request, but only an approval-gated native Kanban tool may
create a task.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT.parents[1] / "nix-os.yaml"
POLICY_PATH = ROOT / "policy.json"
MAX_INPUT = 1_000_000
MAX_OUTPUT = 1_000_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
ACTIVE_STATUSES = {"open", "todo", "triage", "scheduled", "ready", "in_progress", "running", "blocked", "review", "active"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"done", "completed", "cancelled", "failed", "paused", "archived", "retry_exhausted"}
FAILING_CHECKS = {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED"}
PASSING_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class Refusal(RuntimeError):
    """Malformed or ungoverned input; fail closed without external effects."""


def _object(value, label):
    if not isinstance(value, dict):
        raise Refusal(f"{label} must be an object")
    return value


def _read_object(path: Path, label: str):
    try:
        return _object(json.loads(Path(path).read_text(encoding="utf-8")), label)
    except (OSError, ValueError, TypeError) as exc:
        raise Refusal(f"{label} is unavailable or malformed") from exc


def load_governance(manifest_path: Path = DEFAULT_MANIFEST, policy_path: Path = POLICY_PATH):
    manifest = _read_object(manifest_path, "manifest")
    policy = _read_object(policy_path, "policy")
    if not isinstance(manifest.get("projects"), list) or not isinstance(manifest.get("profiles"), list):
        raise Refusal("manifest governance is malformed")
    cap, stale = policy.get("max_fix_attempts"), policy.get("stale_after_hours")
    interval = policy.get("interval_hours")
    if type(cap) is not int or not 1 <= cap <= 20:
        raise Refusal("policy recovery bound is malformed")
    if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not 0 <= stale <= 8760:
        raise Refusal("policy staleness bound is malformed")
    if type(interval) is not int or not 1 <= interval <= 168:
        raise Refusal("policy evidence freshness interval is malformed")
    return manifest, policy


def governed(manifest: dict, project_id: str) -> tuple[str, str]:
    projects = [p for p in manifest["projects"] if isinstance(p, dict) and p.get("id") == project_id]
    if len(projects) != 1:
        raise Refusal("project is not uniquely governed")
    project = projects[0]
    if project.get("paused") is True or project.get("archived") is True:
        raise Refusal("project is paused or archived")
    board, lead = project.get("board"), project.get("lead")
    if not isinstance(board, str) or not IDENT.fullmatch(board) or not isinstance(lead, str) or not IDENT.fullmatch(lead):
        raise Refusal("project board or Lead is not declared")
    profiles = [p for p in manifest["profiles"] if isinstance(p, dict) and p.get("id") == lead
                and p.get("kind") == "lead" and isinstance(p.get("projects"), list)
                and project_id in p["projects"]]
    if len(profiles) != 1:
        raise Refusal("project has no uniquely declared Lead profile")
    return board, lead


def stable_key(event_key: str, action: str) -> str:
    return hashlib.sha256((event_key + "\0" + action).encode("utf-8")).hexdigest()


def retry_allowed(attempts: int, policy: dict) -> bool:
    cap = policy.get("max_fix_attempts")
    return (type(attempts) is int and type(cap) is int and 0 <= attempts < cap)


def task_exists(tasks: list[dict], key: str) -> bool:
    return any(isinstance(task, dict) and task.get("idempotency_key") == key for task in tasks)


def _plan(data: dict):
    allowed = {"project_id", "source_task_id", "source_kind", "event_key", "action", "title", "summary",
               "acceptance_check", "diagnostic_kind", "repo", "pr_number", "check_name"}
    if set(data) - allowed:
        raise Refusal("plan contains unsupported fields")
    required = ("project_id", "source_kind", "event_key", "action", "title", "summary", "acceptance_check")
    if any(not isinstance(data.get(k), str) for k in required):
        raise Refusal("plan is incomplete")
    if not IDENT.fullmatch(data["project_id"]):
        raise Refusal("plan identifier is invalid")
    if data["source_kind"] not in {"blocked_task", "stale_task", "worker_diagnostic", "repo_check"}:
        raise Refusal("source kind is unsupported")
    if not HEX64.fullmatch(data["event_key"]) or not SLUG.fullmatch(data["action"]):
        raise Refusal("plan identity is invalid")
    if data["source_kind"] in {"blocked_task", "stale_task", "worker_diagnostic"}:
        if not isinstance(data.get("source_task_id"), str) or not IDENT.fullmatch(data["source_task_id"]):
            raise Refusal("source task identity is invalid")
    else:
        repo, number, check = data.get("repo"), data.get("pr_number"), data.get("check_name")
        if (not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo)
                or type(number) is not int or number < 1 or not isinstance(check, str)
                or not check.strip() or len(check) > 200 or any(ord(c) < 32 for c in check)):
            raise Refusal("repository-check identity is invalid")
    for name, limit in (("title", 160), ("summary", 2000), ("acceptance_check", 1000)):
        value = data[name]
        if not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise Refusal("plan text is invalid")
    kind = data.get("diagnostic_kind")
    if data["source_kind"] == "worker_diagnostic" and (not isinstance(kind, str) or not SLUG.fullmatch(kind)):
        raise Refusal("diagnostic kind is required")
    return data


def _parse_time(stamp):
    try:
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            return datetime.fromtimestamp(stamp, timezone.utc)
        elif isinstance(stamp, str):
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None


def _age_hours(stamp):
    dt = _parse_time(stamp)
    if dt is None:
        return None
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age if age >= 0 else None


def _evidence_source(source_kind: str) -> str:
    return {
        "blocked_task": "immutable_checkpoint_sqlite",
        "stale_task": "immutable_checkpoint_sqlite",
        "worker_diagnostic": "passive_diagnostic_export",
        "repo_check": "github_checks_read",
    }[source_kind]


def _require_fresh_observation(evidence: dict, policy: dict, label: str, source: str):
    if evidence.get("complete") is not True:
        raise Refusal(f"{label} coverage is incomplete or unverified")
    if evidence.get("source") != source:
        raise Refusal(f"{label} provenance is missing or mismatched")
    age = _age_hours(evidence.get("observed_at"))
    if age is None or age > policy["interval_hours"]:
        raise Refusal(f"{label} is stale, future-dated, or missing its observation time")


def _source_state(plan: dict, policy: dict, evidence: dict) -> str:
    """Return active/resolved/unknown for one fresh, structured source observation."""
    kind = plan["source_kind"]
    if kind in {"blocked_task", "stale_task"}:
        if evidence.get("id") != plan["source_task_id"] or not isinstance(evidence.get("status"), str):
            return "unknown"
        status = evidence["status"].lower()
        if status not in KNOWN_STATUSES:
            return "unknown"
        if kind == "blocked_task":
            return "active" if status == "blocked" else "resolved"
        if status not in ACTIVE_STATUSES:
            return "resolved"
        age = _age_hours(evidence.get("updated_at") or evidence.get("created_at"))
        if age is None:
            return "unknown"
        return "active" if age >= policy["stale_after_hours"] else "resolved"

    if kind == "worker_diagnostic":
        diagnostics = evidence.get("diagnostics")
        if evidence.get("task_id") != plan["source_task_id"] or not isinstance(diagnostics, list):
            return "unknown"
        matches = [d for d in diagnostics if isinstance(d, dict) and d.get("task_id") == plan["source_task_id"]
                   and d.get("kind") == plan["diagnostic_kind"]]
        if len(matches) > 1:
            return "unknown"
        if not matches:
            return "resolved"
        return "active" if matches[0].get("severity") in {"error", "critical"} else "resolved"

    repo_map = policy.get("repos")
    if not isinstance(repo_map, dict) or repo_map.get(plan["project_id"]) != plan["repo"]:
        raise Refusal("repository is not explicitly mapped to this project")
    if evidence.get("repo") != plan["repo"] or evidence.get("pr_number") != plan["pr_number"]:
        return "unknown"
    state = evidence.get("state")
    if state in {"CLOSED", "MERGED"}:
        return "resolved"
    if state != "OPEN":
        return "unknown"
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return "unknown"
    matches = [c for c in checks if isinstance(c, dict)
               and (c.get("name") == plan["check_name"] or c.get("context") == plan["check_name"])]
    if len(matches) != 1:
        return "unknown"
    conclusion = matches[0].get("conclusion") or matches[0].get("state")
    if conclusion in FAILING_CHECKS:
        return "active"
    if conclusion in PASSING_CHECKS:
        return "resolved"
    return "unknown"


def _marker(plan: dict) -> str:
    if plan["source_kind"] == "repo_check":
        source = f"{plan['repo']}#pr{plan['pr_number']}:check={hashlib.sha256(plan['check_name'].encode()).hexdigest()}"
    else:
        source = f"{plan['source_kind']}:{plan['source_task_id']}"
    return f"monitor_recovery event={plan['event_key']} action={plan['action']} source={source}"


def _request(value):
    value = _object(value, "request")
    if set(value) - {"plan", "source_evidence", "verification_evidence", "recovery_history",
                     "recovery_task_id", "recovery_task"}:
        raise Refusal("request contains unsupported fields")
    plan = _plan(_object(value.get("plan"), "plan"))
    evidence = _object(value.get("source_evidence"), "source evidence")
    return plan, evidence


def _validate_recovery_history(history, plan, board, policy):
    history = _object(history, "recovery history")
    if (history.get("board_id") != board or history.get("event_key") != plan["event_key"]
            or history.get("complete") is not True):
        raise Refusal("recovery history is incomplete or does not match the governed event")
    _require_fresh_observation(history, policy, "recovery history", "immutable_checkpoint_sqlite")
    tasks = history.get("tasks")
    if not isinstance(tasks, list):
        raise Refusal("recovery history task list is unavailable")
    seen_ids, seen_keys, seen_actions = set(), set(), set()
    for task in tasks:
        task = _object(task, "recovery history row")
        task_id, status, action = task.get("id"), task.get("status"), task.get("action")
        key = task.get("idempotency_key")
        if (not isinstance(task_id, str) or not IDENT.fullmatch(task_id)
                or not isinstance(status, str) or status.lower() not in KNOWN_STATUSES
                or not isinstance(action, str) or not SLUG.fullmatch(action)
                or type(task.get("max_retries")) is not int
                or task.get("max_retries") != policy["max_fix_attempts"]
                or key != stable_key(plan["event_key"], action)
                or task.get("event_key") != plan["event_key"]):
            raise Refusal("recovery history contains an invalid or mismatched task")
        if task_id in seen_ids or key in seen_keys or action in seen_actions:
            raise Refusal("recovery history contains duplicate attempts")
        if status.lower() in ACTIVE_STATUSES:
            raise Refusal("an existing recovery task is still active")
        seen_ids.add(task_id)
        seen_keys.add(key)
        seen_actions.add(action)
    if task_exists(tasks, stable_key(plan["event_key"], plan["action"])):
        raise Refusal("this recovery action already exists; refusing a duplicate handoff")
    attempts = len(tasks)
    if not retry_allowed(attempts, policy):
        raise Refusal("recovery attempt cap is exhausted")
    return attempts + 1


def prepare(request: dict, manifest: dict, policy: dict):
    plan, evidence = _request(request)
    board, lead = governed(manifest, plan["project_id"])
    _require_fresh_observation(evidence, policy, "source evidence", _evidence_source(plan["source_kind"]))
    if _source_state(plan, policy, evidence) != "active":
        raise Refusal("source condition is not freshly verified")
    attempt_number = _validate_recovery_history(request.get("recovery_history"), plan, board, policy)
    marker = _marker(plan)
    attempts = policy["max_fix_attempts"]
    body = chr(10).join((
        plan["summary"],
        "Acceptance check: " + plan["acceptance_check"],
        f"Recovery attempt: {attempt_number} of {attempts}.",
        f"Recovery bound: at most {attempts} distinct attempts total. "
        "Do not repeat a failed strategy; stop and request human review when the cap is reached.",
        marker,
    ))
    return {"result": "approval_required", "requires_human_approval": True,
            "creation_channel": "approval_gated_native_kanban", "board": board, "assignee": lead,
            "max_fix_attempts": attempts, "max_retries": attempts, "attempt_number": attempt_number,
            "initial_status": "running",
            "idempotency_key": stable_key(plan["event_key"], plan["action"]),
            "title": plan["title"], "body": body, "marker": marker}


def verify(request: dict, manifest: dict, policy: dict):
    plan, _ = _request(request)
    verification_evidence = _object(request.get("verification_evidence"), "verification evidence")
    board, lead = governed(manifest, plan["project_id"])
    task_id = request.get("recovery_task_id")
    task = _object(request.get("recovery_task"), "recovery-task readback")
    _require_fresh_observation(task, policy, "recovery-task readback", "kanban_readback")
    if not isinstance(task_id, str) or not IDENT.fullmatch(task_id):
        raise Refusal("recovery-task identifier is required")
    if task.get("id") != task_id or not isinstance(task.get("status"), str):
        raise Refusal("recovery-task identity readback does not match")
    if task.get("board_id") != board or task.get("assignee") != lead:
        raise Refusal("recovery task is not on the governed board or assigned to its Lead")
    expected_bound = f"Recovery bound: at most {policy['max_fix_attempts']} distinct attempts total."
    if (type(task.get("max_retries")) is not int or task["max_retries"] != policy["max_fix_attempts"]
            or task.get("idempotency_key") != stable_key(plan["event_key"], plan["action"])
            or not isinstance(task.get("body"), str) or _marker(plan) not in task["body"]
            or expected_bound not in task["body"]):
        raise Refusal("recovery-task policy or identity readback does not match")
    status = task["status"].lower()
    if status not in KNOWN_STATUSES:
        return {"result": "unknown", "task_id": task_id, "status": "unknown"}
    if status not in {"done", "completed"}:
        return {"result": "unresolved", "task_id": task_id, "status": status}
    completed_at = _parse_time(task.get("completed_at"))
    task_observed_at = _parse_time(task.get("observed_at"))
    observed_at = _parse_time(verification_evidence.get("observed_at"))
    _require_fresh_observation(verification_evidence, policy, "verification source evidence",
                               _evidence_source(plan["source_kind"]))
    if (completed_at is None or task_observed_at is None or task_observed_at < completed_at
            or observed_at is None or observed_at <= completed_at):
        raise Refusal("task readback and source verification must be observed after recovery-task completion")
    source = _source_state(plan, policy, verification_evidence)
    if source == "resolved":
        return {"result": "verified_resolved", "task_id": task_id, "status": status}
    if source == "active":
        return {"result": "unresolved", "task_id": task_id, "status": status}
    return {"result": "unknown", "task_id": task_id, "status": status}


def execute(request: dict, *, mode="prepare", manifest_path: Path = DEFAULT_MANIFEST,
            policy_path: Path = POLICY_PATH):
    manifest, policy = load_governance(manifest_path, policy_path)
    if mode == "prepare":
        return prepare(request, manifest, policy)
    if mode == "verify":
        return verify(request, manifest, policy)
    raise Refusal("mode must be prepare or verify")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--mode", choices=("prepare", "verify"), required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        if not args.stdin:
            raise Refusal("JSON input requires --stdin")
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw.encode("utf-8")) > MAX_INPUT:
            raise Refusal("input exceeded safety bound")
        request = _object(json.loads(raw), "input")
        result = execute(request, mode=args.mode, manifest_path=args.manifest)
        output = json.dumps(result, separators=(",", ":"))
        if len(output.encode("utf-8")) > MAX_OUTPUT:
            raise Refusal("output exceeded safety bound")
        print(output)
        return 0
    except (OSError, Refusal, ValueError, TypeError) as exc:
        print(json.dumps({"result": "refused", "reason": str(exc)}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
