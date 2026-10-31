import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.monitoring import collector


def _seed_wal_db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, project_id TEXT, assignee TEXT,
            created_at INTEGER NOT NULL, completed_at INTEGER, started_at INTEGER,
            last_heartbeat_at INTEGER,
            body TEXT,
            idempotency_key TEXT,
            max_retries INTEGER
        );
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, created_at INTEGER);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, created_at INTEGER);
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY, task_id TEXT, last_heartbeat_at INTEGER,
            ended_at INTEGER, started_at INTEGER
        );
    """)
    conn.execute("INSERT INTO tasks (id,status,project_id,assignee,created_at,completed_at,started_at,last_heartbeat_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("task-1", "ready", "synthetic", "synthetic-lead", 1_700_000_000, None, None, None))
    conn.execute("INSERT INTO task_events VALUES (?,?,?)", (1, "task-1", 1_700_000_100))
    conn.execute("INSERT INTO task_comments VALUES (?,?,?)", (1, "task-1", 1_700_000_200))
    conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?)", (1, "task-1", 1_700_000_300, None, 1_700_000_250))
    conn.commit()
    return conn


def _file_state(path):
    try:
        st = path.lstat()
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode, st.st_nlink)
    except FileNotFoundError:
        return None


def _digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class SQLiteObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("HERMES_KANBAN_DB", None)

    def test_active_wal_is_unknown_without_touching_any_sidecar(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
        self.assertTrue(wal.exists() and wal.stat().st_size > 0)
        before = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
        rows, record, diagnostics = collector.read_board_tasks("synthetic-board", db)
        after = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
        self.assertEqual(rows, [])
        self.assertEqual(record["status"], "unavailable")
        self.assertEqual(record["unavailable_reason"], "wal_active_or_db_missing")
        self.assertEqual(diagnostics, "unavailable")
        self.assertEqual(before, after)

    def test_checkpointed_read_uses_latest_activity_and_changes_no_files(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)
        before = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
        rows, record, diagnostics = collector.read_board_tasks("synthetic-board", db)
        after = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
        self.assertEqual(record["status"], "available")
        self.assertEqual((record["row_count"], record["included_count"]), (1, 1))
        self.assertEqual(diagnostics, "unavailable")
        self.assertEqual(len(rows), 1)
        sanitized = collector.sanitize_task(rows[0], "synthetic-board")
        self.assertIsNotNone(sanitized)
        assert sanitized is not None
        self.assertEqual(sanitized["updated_at"], collector.safe_time(1_700_000_300))
        self.assertEqual(sanitized["project_id"], "synthetic")
        self.assertEqual(before, after)

    def test_symlinked_database_and_worker_pinned_database_fail_closed(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        alias = self.root / "alias.db"
        alias.symlink_to(db)
        rows, record, _ = collector.read_board_tasks("synthetic-board", alias)
        self.assertEqual((rows, record["status"]), ([], "unavailable"))
        os.environ["HERMES_KANBAN_DB"] = str(db)
        rows, record, _ = collector.read_board_tasks("synthetic-board", db)
        self.assertEqual((rows, record["status"]), ([], "unavailable"))

    def test_invalid_board_id_and_missing_database_fail_closed(self):
        rows, record, _ = collector.read_board_tasks("../escape", self.root / "missing.db")
        self.assertEqual((rows, record["status"]), ([], "unavailable"))
        rows, record, _ = collector.read_board_tasks("synthetic-board", self.root / "missing.db")
        self.assertEqual((rows, record["status"]), ([], "unavailable"))

    def test_noncanonical_path_and_cumulative_source_limit_fail_closed(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        noncanonical = str(self.root) + "/../" + self.root.name + "/kanban.db"
        rows, record, _ = collector.read_board_tasks("synthetic-board", noncanonical)
        self.assertEqual((rows, record["status"]), ([], "unavailable"))
        sidecars = [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]
        total = db.stat().st_size + sum(path.stat().st_size for path in sidecars if path.exists())
        with mock.patch.object(collector, "MAX_BOARD_SOURCE_BYTES", total - 1):
            rows, record, _ = collector.read_board_tasks("synthetic-board", db)
        self.assertEqual((rows, record["status"]), ([], "unavailable"))

    def test_recovery_history_is_complete_checkpointed_and_body_minimized(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        event_key, action = "a" * 64, "repair-worker"
        idempotency_key = hashlib.sha256((event_key + chr(0) + action).encode()).hexdigest()
        marker = f"monitor_recovery event={event_key} action={action} source=blocked_task:src1"
        writer.execute(
            "INSERT INTO tasks (id,status,project_id,assignee,created_at,completed_at,started_at,last_heartbeat_at,body,idempotency_key,max_retries) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("rec1", "failed", "synthetic", "synthetic-lead", 1_700_000_400, 1_700_000_500,
             None, None, "PRIVATE SUMMARY MUST NOT ESCAPE\\n" + marker, idempotency_key, 2))
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rows, record, _ = collector.read_board_tasks("synthetic-board", db)
        history = record["recovery_history"]
        self.assertEqual(history["status"], "available")
        self.assertTrue(history["complete"])
        self.assertEqual(len(history["records"]), 1)
        self.assertEqual(history["records"][0]["event_key"], event_key)
        self.assertEqual(history["records"][0]["idempotency_key"], idempotency_key)
        self.assertEqual(history["records"][0]["max_retries"], 2)
        self.assertNotIn("PRIVATE SUMMARY", repr(history))
        sanitized = collector._sanitize_recovery_history(history, "synthetic-board")
        self.assertTrue(sanitized["complete"])
        self.assertEqual(sanitized["records"][0]["id"], "rec1")

    def test_unmarked_digest_idempotency_key_makes_recovery_history_partial(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        writer.execute(
            "INSERT INTO tasks (id,status,project_id,assignee,created_at,completed_at,started_at,last_heartbeat_at,body,idempotency_key,max_retries) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("rec-unmarked", "failed", "synthetic", "synthetic-lead", 1_700_000_400, 1_700_000_500,
             None, None, "PRIVATE BODY WITHOUT A RECOVERY MARKER", "b" * 64, 2))
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _, record, _ = collector.read_board_tasks("synthetic-board", db)
        history = record["recovery_history"]
        self.assertEqual(history["status"], "partial")
        self.assertFalse(history["complete"])
        self.assertEqual(history["records"], [])
        self.assertNotIn("PRIVATE BODY", repr(history))

    def test_row_limit_plus_one_marks_complete_active_rows_partial(self):
        db = self.root / "kanban.db"
        writer = _seed_wal_db(db)
        self.addCleanup(writer.close)
        writer.executemany("INSERT INTO tasks (id,status,project_id,assignee,created_at,completed_at,started_at,last_heartbeat_at) VALUES (?,?,?,?,?,?,?,?)",
                           [(f"task-{i}", "ready", "synthetic", "synthetic-lead", 1_700_000_000 + i,
                             None, None, None) for i in range(2, collector.MAX_TASK_ROWS + 2)])
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rows, record, _ = collector.read_board_tasks("synthetic-board", db)
        self.assertEqual(len(rows), collector.MAX_TASK_ROWS)
        self.assertEqual(record["row_count"], collector.MAX_TASK_ROWS)
        self.assertTrue(record["cap_reached"])
        self.assertEqual(record["status"], "partial")

    def test_sqlite_connection_is_bound_to_validated_descriptor_across_path_swap(self):
        db = self.root / "kanban.db"
        original = _seed_wal_db(db)
        original.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        original.close()
        replacement = self.root / "replacement.db"
        other = _seed_wal_db(replacement)
        other.execute("INSERT INTO tasks (id,status,project_id,assignee,created_at,completed_at,started_at,last_heartbeat_at) VALUES (?,?,?,?,?,?,?,?)",
                      ("replacement-task", "ready", "synthetic", "synthetic-lead", 1_700_000_400,
                       None, None, None))
        other.commit()
        other.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        other.close()
        saved = self.root / "saved.db"
        original_connect = sqlite3.connect
        swapped = False
        opened_rows = []

        def swapping_connect(database, *args, **kwargs):
            nonlocal swapped
            self.assertTrue(str(database).startswith("file:///proc/self/fd/"))
            os.replace(db, saved)
            os.replace(replacement, db)
            try:
                connection = original_connect(database, *args, **kwargs)
                opened_rows.extend(row[0] for row in connection.execute("SELECT id FROM tasks ORDER BY id"))
            finally:
                os.replace(db, replacement)
                os.replace(saved, db)
            swapped = True
            return connection

        with mock.patch.object(collector.sqlite3, "connect", side_effect=swapping_connect):
            rows, record, _ = collector.read_board_tasks("synthetic-board", db)
        self.assertTrue(swapped)
        self.assertEqual(opened_rows, ["task-1"])
        self.assertEqual(record["status"], "unavailable")
        self.assertEqual(record.get("unavailable_reason"), "source_changed_during_read")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
