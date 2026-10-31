"""Bounded, idempotent Lead recovery decisions; never executes actions or sends alerts."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from automation.monitoring.continuity import normalize


class Refusal(RuntimeError):
    """Malformed policy or evidence; fail closed rather than guessing."""


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reconcile(observation: dict, manifest: dict, *, routes: dict[str, dict], state_path: Path,
              authorized_actions: dict[str, list[str]] | None = None,
              retry_limit: int = 2) -> list[dict]:
    """Create once-only decision envelopes for fresh events and exited workers.

    authorized_actions maps project ID to an explicit allowlist. Protected or
    absent actions yield an approval escalation. This function is preview-only.
    """
    if type(retry_limit) is not int or retry_limit < 0 or retry_limit > 5:
        raise Refusal("retry_limit must be an integer between 0 and 5")
    authorized_actions = authorized_actions or {}
    events = normalize(observation, manifest)
    projects = {p.get("id"): p for p in manifest.get("projects", []) if isinstance(p, dict)}
    roles = {p.get("id"): p for p in manifest.get("profiles", []) if isinstance(p, dict)}
    # Reconciliation fallback: an exited worker can be observed even if no
    # terminal Kanban event was delivered. Only sanitized identifiers/statuses
    # are retained; arbitrary worker output is never propagated.
    stamp = observation.get("observed_at") if isinstance(observation, dict) else None
    known = {e["identity"]["task"] for e in events if e["identity"]["type"] == "kanban_terminal"}
    for worker in observation.get("exited_workers", []) if isinstance(observation, dict) else []:
        if not isinstance(worker, dict):
            continue
        project_id, worker_id, status = worker.get("project_id"), worker.get("id"), worker.get("status")
        project = projects.get(project_id)
        if (not project or project.get("paused") or project.get("archived") or
                not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", worker_id) or
                status not in {"exited", "crashed", "failed", "terminated"} or
                not isinstance(roles.get(project.get("lead")), dict) or roles[project["lead"]].get("kind") != "lead"):
            continue
        if worker_id in known:
            continue
        identity = {"type": "worker_exit", "project": project_id, "worker": worker_id, "status": status}
        events.append({"key": _digest(identity), "identity": identity, "lead": project["lead"], "observed_at": stamp})

    decisions = []
    with sqlite3.connect(state_path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS decisions (event_key TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, decision_json TEXT NOT NULL)")
        for event in events:
            project_id = event["identity"]["project"]
            project = projects[project_id]
            lead = event["lead"]
            route = routes.get(lead)
            if not isinstance(route, dict) or route.get("profile") != lead or route.get("platform") != "discord" or not isinstance(route.get("chat_id"), str) or not route["chat_id"].isdigit():
                raise Refusal("missing or mismatched manifest-declared Lead route")
            prior = db.execute("SELECT attempts FROM decisions WHERE event_key=?", (event["key"],)).fetchone()
            if prior:
                # An explicitly authorized retry may be proposed only while
                # below the global bound; do not automatically execute it.
                if prior[0] >= retry_limit:
                    continue
                continue
            allowed = authorized_actions.get(project_id, [])
            requested = project.get("recovery_action")
            if not isinstance(allowed, list) or any(not isinstance(a, str) for a in allowed):
                raise Refusal("authorized action policy must be an explicit string list")
            if isinstance(requested, str) and requested in allowed:
                decision = {"kind": "authorized_recovery_proposal", "action": requested, "requires_human": False}
            else:
                decision = {"kind": "approval_escalation", "requires_human": True,
                            "blocker": "No explicitly authorized recovery action is available.",
                            "evidence": event["identity"],
                            "requested_decision": "Approve a specific recovery action or choose no action."}
            envelope = {"event_key": event["key"], "lead": lead, "profile": route["profile"],
                        "channel_id": route["chat_id"], "decision": decision, "preview_only": True}
            db.execute("INSERT INTO decisions(event_key,attempts,decision_json) VALUES(?,?,?)",
                       (event["key"], 1, json.dumps(envelope, sort_keys=True)))
            decisions.append(envelope)
        db.commit()
    return decisions
