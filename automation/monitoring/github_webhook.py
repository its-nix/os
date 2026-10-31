"""Pure bounded adapter for native GitHub webhook payloads; never activates routes."""
from __future__ import annotations

import json
import re

MAX_PAYLOAD_BYTES = 256_000
MAX_OUTPUT_BYTES = 4_096
_SHA = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ID = re.compile(r"^[1-9][0-9]{0,29}$")
_CHECK_STATES = {"queued", "in_progress", "completed"}
_CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "timed_out", "action_required", "skipped", "stale"}
_REVIEW_STATES = {"approved", "changes_requested", "commented", "dismissed", "pending"}


def _integer_id(value: object, name: str) -> int:
    if type(value) is not int or value < 1 or value > 10**30 - 1:
        raise ValueError(f"invalid {name}")
    return value


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value.lower()


def infer_event(payload: object) -> str | None:
    """Infer only GitHub event families unambiguously represented by payload shape."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("check_run"), dict):
        return "check_run" if "pull_request" not in payload else None
    if isinstance(payload.get("review"), dict) and isinstance(payload.get("pull_request"), dict):
        return "pull_request_review"
    if isinstance(payload.get("pull_request"), dict):
        return "pull_request"
    return None


def adapt_payload(event: str, payload: dict, *, approved_repository: str) -> dict | None:
    """Return only allowlisted fields, or None for irrelevant/ambiguous events."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds byte limit")
    if not isinstance(approved_repository, str) or not _REPO.fullmatch(approved_repository):
        raise ValueError("invalid approved repository")
    repo_obj = payload.get("repository")
    repo = repo_obj.get("full_name") if isinstance(repo_obj, dict) else None
    if repo != approved_repository:
        return None
    pr = None
    result = {"event": event, "repository": approved_repository}
    action = payload.get("action")
    if event == "check_run":
        if action != "completed":
            return None
        run = payload.get("check_run")
        prs = run.get("pull_requests") if isinstance(run, dict) else None
        if not isinstance(prs, list) or len(prs) != 1 or not isinstance(prs[0], dict):
            return None
        pr = prs[0].get("number")
        head = _sha(run.get("head_sha"), "head_sha")
        run_id = _integer_id(run.get("id"), "check_run_id")
        status, conclusion = run.get("status"), run.get("conclusion")
        if status not in _CHECK_STATES or conclusion not in _CONCLUSIONS:
            raise ValueError("invalid check status or conclusion")
        result.update({"action": action, "head_sha": head, "check_run_id": run_id,
                       "status": status, "conclusion": conclusion})
    elif event == "pull_request_review":
        if action not in {"submitted", "dismissed"}:
            return None
        review, request = payload.get("review"), payload.get("pull_request")
        if not isinstance(review, dict) or not isinstance(request, dict):
            return None
        pr = request.get("number")
        head = request.get("head")
        head_sha = _sha(head.get("sha") if isinstance(head, dict) else None, "head_sha")
        review_id = _integer_id(review.get("id"), "review_id")
        state = review.get("state")
        if state not in _REVIEW_STATES:
            raise ValueError("invalid review state")
        commit_sha = _sha(review.get("commit_id"), "review commit SHA")
        result.update({"action": action, "head_sha": head_sha, "review_id": review_id,
                       "state": state, "commit_sha": commit_sha})
    elif event == "pull_request":
        if action not in {"opened", "reopened", "synchronize", "closed"}:
            return None
        request = payload.get("pull_request")
        if not isinstance(request, dict):
            return None
        pr = request.get("number")
        head = request.get("head")
        result.update({"action": action, "head_sha": _sha(head.get("sha") if isinstance(head, dict) else None, "head_sha")})
    else:
        return None
    if type(pr) is not int or pr < 1:
        return None
    result["pr_number"] = pr
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("adapter output exceeds byte limit")
    return result
