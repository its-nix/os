import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from automation.monitoring.collector import discover, stable_events
from automation.monitoring.monitor_snapshot import compact, main
from automation.monitoring.executive_snapshot import main as executive_main


class SnapshotTests(unittest.TestCase):
    def fixture(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        manifest = {
            "projects": [
                {"id": "new-project", "name": "New Project", "lead": "new-lead", "board": "new-board"},
                {"id": "paused", "name": "Paused", "lead": "new-lead", "board": "paused-board", "paused": True},
            ],
            "profiles": [{"id": "new-lead", "kind": "lead"}],
            "metrics": [{"project": "new-project", "name": "users", "source": "users"}],
        }
        runtime = {
            "profiles": [{"name": "new-lead", "kind": "lead"}, {"name": "runtime-worker", "kind": "worker"}],
            "boards": [{"id": "new-board"}, {"id": "paused-board", "paused": True}, {"id": "mystery-board"}],
            "tasks": [
                {"id": "blocked-1", "board_id": "new-board", "status": "blocked", "created_at": "2026-01-09T00:00:00Z", "body": "PRIVATE"},
                {"id": "stale-1", "board_id": "new-board", "status": "ready", "created_at": "2026-01-01T00:00:00Z"},
                {"id": "done-1", "board_id": "new-board", "status": "done"},
                {"id": "unclassified-1", "board_id": "mystery-board", "status": "blocked"},
            ],
            "metrics": {"users": 7},
            "status": {"gateway": "available", "scheduler": "available"},
            "board_list_status": "available",
            "task_read_mode": "immutable_checkpoint_only",
            "task_collection_status": {"new-board": {"status": "available", "row_count": 3, "included_count": 3,
                                    "invalid_row_count": 0, "limit": 20000, "cap_reached": False,
                                    "read_mode": "immutable_checkpoint_only", "wal_bytes": 0}},
            "repos": {},
        }
        report = discover(manifest, runtime, now=now)
        report["events"] = stable_events(report)
        return manifest, runtime, report

    def test_dynamic_project_is_compacted_and_paused_is_excluded(self):
        _, _, report = self.fixture()
        snapshot = compact(report, max_samples=1)
        projects = {p["project"]["id"]: p for p in snapshot["projects"]}
        self.assertIn("new-project", projects)
        self.assertEqual(projects["new-project"]["project"]["lead"], "new-lead")
        self.assertEqual(projects["new-project"]["project"]["scope"], "governed")
        self.assertEqual(projects["new-project"]["task_collection"], {"status": "available", "row_count": 3,
                         "included_count": 3, "invalid_row_count": 0, "limit": 20000,
                         "cap_reached": False, "read_mode": "immutable_checkpoint_only", "wal_bytes": 0})
        self.assertEqual(snapshot["task_read_mode"], "immutable_checkpoint_only")
        self.assertEqual(snapshot["board_list_status"], "available")
        self.assertEqual(projects["new-project"]["kpi_status"], "observed")
        self.assertEqual(projects["new-project"]["kpis"]["users"], {"value": 7, "status": "observed"})
        self.assertEqual(projects["new-project"]["blocked_stale_task_sample"][0]["id"], "blocked-1")
        self.assertTrue(projects["new-project"]["blocked_stale_sample_truncated"])
        self.assertEqual(projects["new-project"]["blocked_stale_finding_count"], 2)
        self.assertEqual(len(projects["new-project"]["blocked_stale_findings_digest"]), 64)
        self.assertNotIn("paused", projects)
        self.assertNotIn("new-board", projects)
        self.assertEqual(snapshot["scope"]["governed_projects"], ["new-project"])
        self.assertEqual(snapshot["scope"]["excluded_paused_or_archived_projects"], ["paused"])
        self.assertIn("runtime-worker", snapshot["scope"]["unclassified_roles"])
        self.assertEqual(projects["mystery-board"]["project"]["scope"], "unclassified")
        self.assertEqual(projects["mystery-board"]["kpi_status"], "not_declared")
        encoded = json.dumps(snapshot)
        self.assertNotIn("PRIVATE", encoded)
        self.assertEqual(len(snapshot["events"]), 2)

    def test_task_collection_unavailable_reason_is_allowlisted_not_path(self):
        manifest, runtime, _ = self.fixture()
        runtime["tasks"] = []
        runtime["task_collection_status"] = {
            "new-board": {"status": "unavailable", "row_count": 0, "included_count": 0,
                          "invalid_row_count": 0, "limit": 20000, "cap_reached": False,
                          "read_mode": "immutable_checkpoint_only", "unavailable_reason": "wal_active_or_db_missing",
                          "db_path": "/private/location"}
        }
        snapshot = compact(discover(manifest, runtime))
        project = next(p for p in snapshot["projects"] if p["project"]["id"] == "new-project")
        self.assertEqual(project["task_collection"]["status"], "unavailable")
        self.assertEqual(project["task_collection"]["unavailable_reason"], "wal_active_or_db_missing")
        self.assertNotIn("db_path", json.dumps(snapshot))
        self.assertNotIn("/private/location", json.dumps(snapshot))

    def test_omitted_finding_identity_changes_bounded_snapshot_digest(self):
        manifest, runtime, _ = self.fixture()
        report_a = discover(manifest, runtime, now=datetime(2026, 1, 10, tzinfo=timezone.utc))
        first = compact(report_a, max_samples=1)["projects"]
        project_a = next(p for p in first if p["project"]["id"] == "new-project")
        changed = {**runtime, "tasks": [dict(task) for task in runtime["tasks"]]}
        next(task for task in changed["tasks"] if task["id"] == "stale-1")["id"] = "stale-2"
        report_b = discover(manifest, changed, now=datetime(2026, 1, 10, tzinfo=timezone.utc))
        project_b = next(p for p in compact(report_b, max_samples=1)["projects"] if p["project"]["id"] == "new-project")
        self.assertEqual(project_a["blocked_stale_finding_count"], project_b["blocked_stale_finding_count"])
        self.assertNotEqual(project_a["blocked_stale_findings_digest"], project_b["blocked_stale_findings_digest"])

    def test_worker_diagnostics_are_bounded_and_omitted_identity_changes_digest(self):
        manifest, runtime, _ = self.fixture()
        runtime["worker_diagnostics_status"] = {"new-board": "available"}
        runtime["worker_diagnostics"] = [
            {"board_id": "new-board", "task_id": "blocked-1", "status": "blocked",
             "kind": f"kind-{i:03d}", "severity": "error", "count": 1, "detail": "PRIVATE"}
            for i in range(101)
        ]
        first = compact(discover(manifest, runtime))
        project_a = next(p for p in first["projects"] if p["project"]["id"] == "new-project")
        self.assertEqual(project_a["worker_health"]["status"], "degraded")
        self.assertEqual(project_a["worker_health"]["diagnostic_count"], 101)
        self.assertTrue(project_a["worker_health"]["sample_truncated"])
        self.assertEqual(len(project_a["worker_health"]["diagnostics"]), 100)
        self.assertEqual(len([e for e in first["events"] if e.get("kind") == "worker_diagnostic"]), 100)
        changed = {**runtime, "worker_diagnostics": [dict(item) for item in runtime["worker_diagnostics"]]}
        next(item for item in changed["worker_diagnostics"] if item["kind"] == "kind-100")["kind"] = "kind-zzz"
        second = compact(discover(manifest, changed))
        project_b = next(p for p in second["projects"] if p["project"]["id"] == "new-project")
        self.assertEqual(project_a["worker_health"]["diagnostics"], project_b["worker_health"]["diagnostics"])
        self.assertNotEqual(project_a["worker_health"]["diagnostic_digest"], project_b["worker_health"]["diagnostic_digest"])
        self.assertNotIn("PRIVATE", json.dumps(first))

    def test_snapshot_bytes_stable_when_only_task_age_advances(self):
        manifest, runtime, _ = self.fixture()
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        first = compact(discover(manifest, runtime, now=now))
        second = compact(discover(manifest, runtime, now=now + timedelta(hours=12)))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertNotIn("age_hours", json.dumps(first))

    def test_cli_fixture_cycle_writes_heartbeat_only_after_snapshot(self):
        manifest, runtime, _ = self.fixture()
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            runtime_path = root / "runtime.json"
            heartbeat = root / "state" / "heartbeat"
            manifest_path.write_text(json.dumps(manifest))
            runtime_path.write_text(json.dumps(runtime))
            output = []
            with patch("builtins.print", side_effect=lambda value: output.append(value)):
                code = main(["--manifest", str(manifest_path), "--runtime", str(runtime_path), "--heartbeat", str(heartbeat)])
            self.assertEqual(code, 0)
            parsed = json.loads(output[0])
            self.assertEqual(parsed["projects"][0]["project"]["id"], "mystery-board")
            self.assertEqual(heartbeat.read_text(), "collector-ok\n")
            self.assertEqual(heartbeat.stat().st_mode & 0o777, 0o600)

    def test_weekly_executive_snapshot_never_refreshes_monitor_heartbeat(self):
        manifest, runtime, _ = self.fixture()
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            runtime_path = root / "runtime.json"
            manifest_path.write_text(json.dumps(manifest))
            runtime_path.write_text(json.dumps(runtime))
            output = []
            with patch("builtins.print", side_effect=lambda value: output.append(value)), patch(
                "automation.monitoring.monitor_snapshot.write_heartbeat", side_effect=AssertionError("must not write")
            ):
                code = executive_main(["--manifest", str(manifest_path), "--runtime", str(runtime_path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output[0])["collection"], "read-only")
            self.assertFalse((root / "state" / "heartbeat").exists())

    def test_symlink_heartbeat_leaf_rejected_without_overwriting_target(self):
        from automation.monitoring.monitor_snapshot import write_heartbeat
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("keep")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                write_heartbeat(link)
            self.assertEqual(target.read_text(), "keep")

    def test_symlink_heartbeat_parent_rejected(self):
        from automation.monitoring.monitor_snapshot import write_heartbeat
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "real"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                write_heartbeat(link / "heartbeat")
            self.assertFalse((target / "heartbeat").exists())

    def test_hardlink_heartbeat_leaf_rejected(self):
        from automation.monitoring.monitor_snapshot import write_heartbeat
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("preserve")
            target.chmod(0o600)
            link = root / "heartbeat"
            link.hardlink_to(target)
            with self.assertRaisesRegex(ValueError, "leaf is unsafe"):
                write_heartbeat(link)
            self.assertEqual(target.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
