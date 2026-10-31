"""Normalize sanitized Kanban/GitHub observations into stable Lead signals.

This module is deliberately policy-free: it never performs recovery actions.
It normalizes bounded native Kanban terminal observations and GitHub checks into
manifest-bound Lead signals. This is distinct from collector.stable_events,
which stabilizes aggregate monitoring report events, and github_signals, which
normalizes individual GitHub webhook evidence into preview route envelopes.
This helper is not currently wired to a runtime route or delivery path.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

SHA = re.compile(r"^[0-9a-f]{40}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
TERMINAL = {"done", "completed", "failed", "cancelled", "blocked", "review"}
BAD_CHECKS = {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED"}
GOOD_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _key(identity: dict) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize(observation: dict, manifest: dict, *, now: datetime | None = None) -> list[dict]:
    """Return only fresh, unambiguous terminal events for manifest-governed Leads.

    Task/PR bodies, titles, URLs, branch names and private check output are never
    copied into a signal. CI evidence must name the exact 40-hex PR head SHA.
    """
    if not isinstance(observation, dict) or not isinstance(manifest, dict):
        return []
    now = now or datetime.now(timezone.utc)
    projects = {p.get("id"): p for p in manifest.get("projects", []) if isinstance(p, dict)}
    roles = {p.get("id"): p for p in manifest.get("profiles", []) if isinstance(p, dict)}
    observed = observation.get("observed_at")
    if not isinstance(observed, str):
        return []
    try:
        stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if stamp.tzinfo is None or (now - stamp).total_seconds() < 0 or (now - stamp).total_seconds() > 3600:
            return []
    except (TypeError, ValueError):
        return []
    events = []
    for task in observation.get("tasks", []):
        if not isinstance(task, dict):
            continue
        project_id, task_id, status = task.get("project_id"), task.get("id"), task.get("status")
        project = projects.get(project_id)
        if (not project or project.get("paused") or project.get("archived") or
                not isinstance(task_id, str) or not ID.fullmatch(task_id) or status not in TERMINAL):
            continue
        lead = project.get("lead")
        if not isinstance(roles.get(lead), dict) or roles[lead].get("kind") != "lead":
            continue
        identity = {"type": "kanban_terminal", "project": project_id, "task": task_id, "status": status}
        events.append({"key": _key(identity), "identity": identity, "lead": lead, "observed_at": observed})
    for check in observation.get("checks", []):
        if not isinstance(check, dict):
            continue
        project_id, repo, number, head = check.get("project_id"), check.get("repo"), check.get("pr_number"), check.get("head_sha")
        project = projects.get(project_id)
        if (not project or project.get("paused") or project.get("archived") or
                not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or
                type(number) is not int or number < 1 or not isinstance(head, str) or not SHA.fullmatch(head)):
            continue
        if check.get("pr_state") != "OPEN" or check.get("observed_head_sha") != head:
            continue
        conclusion = check.get("conclusion")
        if conclusion not in BAD_CHECKS | GOOD_CHECKS:
            continue
        lead = project.get("lead")
        if not isinstance(roles.get(lead), dict) or roles[lead].get("kind") != "lead":
            continue
        identity = {"type": "github_check", "project": project_id, "repo": repo,
                    "pr_number": number, "head_sha": head, "conclusion": conclusion}
        events.append({"key": _key(identity), "identity": identity, "lead": lead, "observed_at": observed})
    return sorted({event["key"]: event for event in events}.values(), key=lambda event: event["key"])
