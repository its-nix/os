#!/usr/bin/env python3
"""Produce a bounded sanitized portfolio snapshot for scheduled interpretation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

try:
    from .collector import discover, load_json, run_readonly_live, stable_events
except ImportError:
    from collector import discover, load_json, run_readonly_live, stable_events

MAX_OUTPUT_BYTES = 96 * 1024
MAX_SAMPLES_PER_PROJECT = 10


def compact(report: dict, max_samples: int = MAX_SAMPLES_PER_PROJECT) -> dict:
    """Keep aggregate portfolio facts and bounded actionable task metadata only."""
    events = stable_events(report)
    event_by_task = {(e["project"], e["task"]["id"]): e["key"] for e in events if isinstance(e.get("task"), dict)}
    compact_events = []
    for event in events:
        if event.get("kind") == "repo_checks":
            compact_events.append({k: event[k] for k in ("key", "project", "kind", "status", "count") if k in event})
        elif event.get("kind") == "repo_check":
            compact_events.append({k: event[k] for k in ("key", "project", "kind", "status", "repo", "pr_number", "check_name", "conclusion") if k in event})
        elif event.get("kind") == "worker_diagnostic":
            compact_events.append({k: event[k] for k in ("key", "project", "kind", "task_id", "diagnostic_kind", "severity", "count") if k in event})
    projects = []
    for item in report.get("projects", []):
        project = item.get("project", {})
        pid = project.get("id")
        candidates = [t for t in item.get("tasks", []) if t.get("status") == "blocked" or t.get("stale") is True]
        candidates.sort(key=lambda t: (t.get("status") != "blocked", -(t.get("age_hours") or 0), t.get("id", "")))
        sample = []
        for task in candidates[:max_samples]:
            clean = {k: task[k] for k in ("id", "status", "assignee", "stale", "created_at", "updated_at") if k in task}
            sample.append(clean)
            event_key = event_by_task.get((pid, task.get("id")))
            if event_key:
                compact_event = {"key": event_key, "project": pid, "task_id": task["id"], "status": task["status"]}
                for field in ("created_at", "updated_at"):
                    if field in task:
                        compact_event[field] = task[field]
                compact_events.append(compact_event)
        candidate_keys = sorted(
            event_by_task.get((pid, task.get("id")), f"{pid}:{task.get('id')}:{task.get('status')}")
            for task in candidates
        )
        findings_digest = hashlib.sha256(
            json.dumps(candidate_keys, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        kpis = item.get("kpis", {})
        if not isinstance(kpis, dict):
            kpis = {}
        if not kpis:
            kpi_status = "not_declared"
        elif all(isinstance(v, dict) and v.get("status") == "unknown" for v in kpis.values()):
            kpi_status = "unknown"
        elif any(isinstance(v, dict) and v.get("status") == "unknown" for v in kpis.values()):
            kpi_status = "partial"
        else:
            kpi_status = "observed"
        task_collection = item.get("task_collection", {})
        if not isinstance(task_collection, dict):
            task_collection = {}
        task_collection_clean = {"status": item.get("task_collection_status") if item.get("task_collection_status") in {"available", "partial", "unavailable", "unknown"} else "unknown"}
        for key in ("row_count", "included_count", "invalid_row_count", "limit", "wal_bytes"):
            value = task_collection.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                task_collection_clean[key] = value
        if isinstance(task_collection.get("cap_reached"), bool):
            task_collection_clean["cap_reached"] = task_collection["cap_reached"]
        if task_collection.get("read_mode") == "immutable_checkpoint_only":
            task_collection_clean["read_mode"] = "immutable_checkpoint_only"
        reason = task_collection.get("unavailable_reason")
        if isinstance(reason, str) and (reason in {"invalid_path", "invalid_board_or_database_pin", "wal_active_or_db_missing", "source_changed_during_read"}
                                        or reason in {"source_error_OSError", "source_error_ValueError", "source_error_DatabaseError"}):
            task_collection_clean["unavailable_reason"] = reason
        history = item.get("recovery_history", {})
        if not isinstance(history, dict):
            history = {}
        history_status = history.get("status") if history.get("status") in {"available", "partial", "unavailable"} else "unknown"
        history_records = []
        history_invalid = False
        raw_history_records = history.get("records", [])
        if not isinstance(raw_history_records, list):
            raw_history_records = []
            history_invalid = True
        history_capped = len(raw_history_records) > 50 or history.get("cap_reached") is True
        for record in raw_history_records[:50]:
            if not isinstance(record, dict):
                history_invalid = True
                continue
            event_key, action = record.get("event_key"), record.get("action")
            key = record.get("idempotency_key")
            expected = hashlib.sha256((event_key + "\0" + action).encode()).hexdigest() if isinstance(event_key, str) and isinstance(action, str) else None
            if (not isinstance(record.get("id"), str) or not isinstance(record.get("status"), str)
                    or key != expected or type(record.get("max_retries")) is not int):
                history_invalid = True
                continue
            history_records.append({k: record[k] for k in (
                "id", "status", "event_key", "action", "idempotency_key", "max_retries", "created_at", "completed_at"
            ) if k in record})
        if (history_invalid or history_capped) and history_status == "available":
            history_status = "partial"
        history_complete = (history_status == "available" and history.get("complete") is True
                            and not history_invalid and not history_capped)
        history_clean = {
            "board_id": project.get("board"),
            "status": history_status,
            "source": history.get("source") if history.get("source") == "immutable_checkpoint_sqlite" else "unknown",
            "observed_at": history.get("observed_at") if isinstance(history.get("observed_at"), str) else None,
            "complete": history_complete,
            "limit": 50,
            "cap_reached": history_capped,
            "records": history_records,
        }
        projects.append({
            "project": {k: project[k] for k in ("id", "name", "board", "lead", "scope") if k in project},
            "task_count": item.get("task_count", 0),
            "task_collection": task_collection_clean,
            "recovery_history": history_clean,
            "blocked_count": item.get("blocked_count", 0),
            "stale_count": item.get("stale_count", 0),
            "blocked_stale_finding_count": len(candidates),
            "blocked_stale_findings_digest": findings_digest,
            "running_count": item.get("running_count", 0),
            "completed_count_7d_observed": item.get("completed_count_7d_observed", 0),
            "kpi_status": kpi_status,
            "kpis": kpis,
            "worker_health": item.get("worker_health", {"status": "unknown", "source_status": "unknown"}),
            "blocked_stale_task_sample": sample,
            "blocked_stale_sample_truncated": len(candidates) > len(sample),
        })
    scope = dict(report.get("scope", {}))
    all_governed = scope.get("governed_projects", [])
    active_governed = sorted(item.get("project", {}).get("id") for item in report.get("projects", [])
                             if item.get("project", {}).get("scope") == "governed")
    scope["governed_projects_all"] = all_governed
    scope["governed_projects"] = active_governed
    scope["excluded_paused_or_archived_projects"] = sorted(set(all_governed) - set(active_governed))
    return {
        "schema_version": report.get("schema_version", 1),
        "observed_at": report.get("observed_at") if isinstance(report.get("observed_at"), str) else None,
        "collection": "read-only",
        "scope": scope,
        "board_list_status": report.get("board_list_status", "unknown") if report.get("board_list_status") in {"available", "partial", "unavailable", "unknown"} else "unknown",
        "task_read_mode": report.get("task_read_mode") if report.get("task_read_mode") == "immutable_checkpoint_only" else "unknown",
        "profiles": report.get("profiles", []),
        "runtime_status": report.get("runtime_status", {}),
        "projects": projects,
        "repos": report.get("repos", {}),
        "repo_status": report.get("repo_status", {}),
        "repo_collection": report.get("repo_collection", {}),
        "events": compact_events,
    }


def _heartbeat_parent(path: Path) -> tuple[Path, int]:
    """Validate every ancestor, then bind operations to the private parent directory."""
    path = Path(os.path.abspath(path))
    home = Path(os.path.abspath(Path.home()))
    if not path.is_relative_to(home) or path.name in {"", ".", ".."}:
        raise ValueError("heartbeat path must be a file beneath the current user's home")
    current = Path(path.anchor)
    for part in path.parent.parts[1:]:
        current /= part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError("heartbeat path contains a symlink or non-directory")
        if (current == home or home in current.parents) and st.st_uid != os.getuid():
            raise ValueError("heartbeat path is not owned by the current user")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        dir_fd = os.open(path.parent, flags)
    except OSError as exc:
        raise ValueError("heartbeat directory is unsafe") from exc
    st = os.fstat(dir_fd)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        os.close(dir_fd)
        raise ValueError("heartbeat directory must be private and user-owned")
    return path, dir_fd


def write_heartbeat(path: Path) -> None:
    path, dir_fd = _heartbeat_parent(path)
    name = path.name
    temp_name = f".monitor-heartbeat-{secrets.token_hex(12)}"
    try:
        try:
            existing = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.getuid()
            or existing.st_nlink != 1 or stat.S_IMODE(existing.st_mode) != 0o600
        ):
            raise ValueError("heartbeat leaf is unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        try:
            remaining = memoryview(b"collector-ok\n")
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise ValueError("heartbeat write was incomplete")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        os.close(dir_fd)


def build_snapshot(manifest_path: Path, runtime_path: Path | None) -> dict:
    manifest = load_json(manifest_path)
    runtime = load_json(runtime_path) if runtime_path else run_readonly_live()
    raw = discover(manifest, runtime)
    raw["events"] = stable_events(raw)
    return compact(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path.cwd() / "nix-os.yaml")
    parser.add_argument("--runtime", type=Path, help="synthetic JSON fixture; live collection is skipped")
    parser.add_argument("--heartbeat", type=Path, default=Path.home() / ".local/state/hermes-monitoring/monitor-cycle-heartbeat")
    args = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(args.manifest, args.runtime)
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("sanitized portfolio snapshot exceeds configured output bound")
        print(payload)
        write_heartbeat(args.heartbeat)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "unavailable", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
