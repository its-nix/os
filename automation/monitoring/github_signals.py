"""Normalize GitHub check and review updates for profile-bound Lead routes.

Pure normalization and preview helpers only. Constructing a deterministic
route envelope does not subscribe, deliver, wake a Lead, or suppress replays.
The owning Lead decides and records evidence in native Kanban. Durable replay
suppression/delivery must be provided by the native route/task path and verified
separately; this module neither creates state nor activates routes.
"""
from __future__ import annotations

import hashlib
import json
import re

_SHA = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _required_text(value: object, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if pattern is not None and isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError(f"invalid {field}")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def normalize_signal(payload: dict, *, repository: str, pr_number: int) -> dict:
    """Return bounded check/review evidence keyed to the immutable head SHA."""
    repo = _required_text(repository, "repository", _REPO)
    if type(pr_number) is not int or pr_number < 1:
        raise ValueError("invalid pr_number")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    kind = payload.get("kind")
    if kind == "check":
        source_id = _required_text(payload.get("check_run_id"), "check_run_id", re.compile(r"^[0-9]{1,30}$"))
        state = _required_text(payload.get("conclusion") or payload.get("status"), "check state").lower()
        if state not in {"queued", "in_progress", "completed", "success", "failure", "cancelled", "timed_out", "neutral", "action_required", "skipped"}:
            raise ValueError("invalid check state")
        subject = "check_run_id"
        evidence_id = source_id
        status = state
    elif kind == "review":
        source_id = _required_text(payload.get("review_id"), "review_id", re.compile(r"^[0-9]{1,30}$"))
        status = _required_text(payload.get("state"), "review state").lower()
        if status not in {"approved", "changes_requested", "commented", "dismissed", "pending"}:
            raise ValueError("invalid review state")
        subject = "review_id"
        evidence_id = source_id
    else:
        raise ValueError("unsupported signal kind")

    sha = _required_text(payload.get("head_sha"), "head_sha", _SHA).lower()
    identity = ["github", repo, pr_number, kind, evidence_id, sha, status]
    event_key = hashlib.sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return {
        "schema_version": 1,
        "source": "github",
        "kind": kind,
        "repository": repo,
        "pr_number": pr_number,
        "head_sha": sha,
        subject: evidence_id,
        "status": status,
        "event_key": event_key,
    }


def lead_route_envelope(
    signal: dict, *, parent_task_id: str, lead_profile: str, cron_job: str
) -> dict:
    """Build a deterministic preview envelope; this does not enforce idempotency.

    The correlation_key is a hint for an existing native route/task path. It does
    not enforce dedupe or suppress duplicate delivery/work.
    """
    for value, field in ((parent_task_id, "parent_task_id"), (lead_profile, "lead_profile"), (cron_job, "cron_job")):
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) is None:
            raise ValueError(f"invalid {field}")
    if not isinstance(signal, dict) or signal.get("source") != "github":
        raise ValueError("invalid signal")
    if _SHA.fullmatch(str(signal.get("head_sha", ""))) is None:
        raise ValueError("signal must identify an exact head SHA")
    if re.fullmatch(r"[0-9a-f]{64}", str(signal.get("event_key", ""))) is None:
        raise ValueError("invalid event_key")
    action_key = hashlib.sha256(f"{parent_task_id}\0{signal['event_key']}".encode()).hexdigest()
    return {
        "route_profile": lead_profile,
        "cron_job": cron_job,
        "parent_task_id": parent_task_id,
        "event_key": signal["event_key"],
        "correlation_key": action_key,
        "preview_only": True,
        "evidence": {
            key: signal[key]
            for key in ("source", "kind", "repository", "pr_number", "head_sha", "check_run_id", "review_id", "status")
            if key in signal
        },
    }


def accept_once(signal: dict, seen: set[str]) -> bool:
    """In-process test helper only; not durable replay protection."""
    key = signal.get("event_key") if isinstance(signal, dict) else None
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise ValueError("invalid event_key")
    if key in seen:
        return False
    seen.add(key)
    return True
