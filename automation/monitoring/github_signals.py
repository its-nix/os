"""Normalize GitHub check and review updates for profile-bound Lead routes.

Pure normalization and preview helpers only. Constructing a deterministic
route envelope does not subscribe, deliver, wake a Lead, or suppress replays.
The owning Lead decides and records evidence in native Kanban. Durable replay
suppression/delivery must be provided by the native route/task path and verified
separately; this module neither creates state nor activates routes.
"""
from __future__ import annotations

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
    return {
        "schema_version": 1,
        "source": "github",
        "kind": kind,
        "repository": repo,
        "pr_number": pr_number,
        "head_sha": sha,
        subject: evidence_id,
        "status": status,
    }
