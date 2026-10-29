import hashlib
import sqlite3
from pathlib import Path

from automation.monitoring import collector


def _seed_wal_db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, project_id TEXT, assignee TEXT,
            created_at INTEGER NOT NULL, completed_at INTEGER, started_at INTEGER,
            last_heartbeat_at INTEGER
        );
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, created_at INTEGER);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, created_at INTEGER);
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY, task_id TEXT, last_heartbeat_at INTEGER,
            ended_at INTEGER, started_at INTEGER
        );
    """)
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
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


def test_active_wal_is_unknown_without_touching_any_sidecar(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    db = tmp_path / "kanban.db"
    writer = _seed_wal_db(db)
    wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
    assert wal.exists() and wal.stat().st_size > 0
    before = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
    rows, record, diagnostics = collector.read_board_tasks("synthetic-board", db)
    after = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
    assert rows == []
    assert record["status"] == "unavailable"
    assert record["unavailable_reason"] == "wal_active_or_db_missing"
    assert diagnostics == "unavailable"
    assert before == after
    writer.close()


def test_checkpointed_read_uses_latest_activity_and_changes_no_files(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    db = tmp_path / "kanban.db"
    writer = _seed_wal_db(db)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
    assert not wal.exists() or wal.stat().st_size == 0
    before = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
    rows, record, diagnostics = collector.read_board_tasks("synthetic-board", db)
    after = (_file_state(db), _file_state(wal), _file_state(shm), _digest(db), _digest(wal), _digest(shm))
    assert record["status"] == "available"
    assert record["row_count"] == 1 and record["included_count"] == 1
    assert diagnostics == "unavailable"
    assert len(rows) == 1
    sanitized = collector.sanitize_task(rows[0], "synthetic-board")
    assert sanitized is not None
    assert sanitized["updated_at"] == collector.safe_time(1_700_000_300)
    assert sanitized["project_id"] == "synthetic"
    assert before == after
    writer.close()


def test_symlinked_database_and_worker_pinned_database_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    db = tmp_path / "kanban.db"
    writer = _seed_wal_db(db)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    alias = tmp_path / "alias.db"
    alias.symlink_to(db)
    rows, record, _ = collector.read_board_tasks("synthetic-board", alias)
    assert rows == [] and record["status"] == "unavailable"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    rows, record, _ = collector.read_board_tasks("synthetic-board", db)
    assert rows == [] and record["status"] == "unavailable"
    writer.close()


def test_invalid_board_id_and_missing_database_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    rows, record, _ = collector.read_board_tasks("../escape", tmp_path / "missing.db")
    assert rows == [] and record["status"] == "unavailable"
    rows, record, _ = collector.read_board_tasks("synthetic-board", tmp_path / "missing.db")
    assert rows == [] and record["status"] == "unavailable"


def test_noncanonical_path_and_cumulative_source_limit_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    db = tmp_path / "kanban.db"
    writer = _seed_wal_db(db)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    noncanonical = str(tmp_path) + "/../" + tmp_path.name + "/kanban.db"
    rows, record, _ = collector.read_board_tasks("synthetic-board", noncanonical)
    assert rows == [] and record["status"] == "unavailable"
    sidecars = [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]
    total = db.stat().st_size + sum(path.stat().st_size for path in sidecars if path.exists())
    monkeypatch.setattr(collector, "MAX_BOARD_SOURCE_BYTES", total - 1)
    rows, record, _ = collector.read_board_tasks("synthetic-board", db)
    assert rows == [] and record["status"] == "unavailable"
    writer.close()
