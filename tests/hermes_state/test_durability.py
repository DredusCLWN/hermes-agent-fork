"""Durability tests for SessionDB storage layer.

Tests that simulate crash mid-write and verify integrity on reopen:
- Kill process (os._exit) during message append → reopen → integrity_check
- WAL checkpoint survives crash
- FTS5 consistency after crash (orphaned triggers, missing rows)
- Concurrent writers don't corrupt the database
- Schema version survives crash
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _integrity_ok(conn) -> bool:
    """Run PRAGMA integrity_check and return True if 'ok'."""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] == "ok"


def test_basic_integrity_after_normal_close(db, tmp_path):
    """Baseline: a normal open/write/close cycle passes integrity_check."""
    db.create_session("s1", source="cli")
    db.close()

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "state.db"))
    try:
        assert _integrity_ok(conn)
    finally:
        conn.close()


def test_crash_mid_write_reopens_cleanly(tmp_path):
    """Simulate process crash (os._exit) mid-write and verify reopen integrity.

    Spawns a child process that writes data and exits abruptly with os._exit(1)
    without closing the connection. The parent then reopens the DB and checks
    integrity, WAL recovery, and data visibility.
    """
    db_path = tmp_path / "state.db"
    root_path = str(Path(__file__).resolve().parents[2])
    db_path_str = str(db_path)

    # Phase 1: create and populate in a child that crashes
    child_code = f"""
import sys, os
sys.path.insert(0, {root_path!r})
from hermes_state import SessionDB
from pathlib import Path
db = SessionDB(Path({db_path_str!r}))
db.create_session("crash-test", source="cli")
db.append_messages_batch("crash-test", [
    {{"role": "user", "content": "message before crash"}},
    {{"role": "assistant", "content": "response before crash"}},
])
# Abrupt exit — no close(), no WAL checkpoint, no atexit
os._exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1, f"Child should have crashed, got rc={result.returncode}"

    # Phase 2: reopen and verify
    db = SessionDB(db_path)
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            assert _integrity_ok(conn), "integrity_check failed after crash"
        finally:
            conn.close()

        # Session should exist
        session = db.get_session("crash-test")
        assert session is not None, "session missing after crash"

        # Messages should be readable (WAL replay should recover committed data)
        messages = db.get_messages("crash-test")
        assert len(messages) >= 1, f"expected messages after crash, got {len(messages)}"
    finally:
        db.close()


def test_fts5_consistency_after_crash(tmp_path):
    """FTS5 index stays consistent with base table after crash.

    Orphaned FTS triggers or missing FTS rows after a crash would cause
    session_search to return stale or incomplete results.
    """
    db_path = tmp_path / "state.db"
    root_path = str(Path(__file__).resolve().parents[2])
    db_path_str = str(db_path)

    child_code = f"""
import sys, os
sys.path.insert(0, {root_path!r})
from hermes_state import SessionDB
from pathlib import Path
db = SessionDB(Path({db_path_str!r}))
db.create_session("fts-crash", source="cli")
for i in range(10):
    db.append_messages_batch("fts-crash", [
        {{"role": "user", "content": f"searchable message number {{i}}"}},
    ])
os._exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1

    db = SessionDB(db_path)
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            assert _integrity_ok(conn)

            # Check FTS table exists and has rows
            fts_count = conn.execute(
                "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'searchable'"
            ).fetchone()[0]
            msg_count = conn.execute(
                "SELECT count(*) FROM messages WHERE content LIKE '%searchable%'"
            ).fetchone()[0]
            assert fts_count == msg_count, (
                f"FTS row count ({fts_count}) != base table count ({msg_count}) — "
                f"FTS index is inconsistent after crash"
            )
        finally:
            conn.close()
    finally:
        db.close()


def test_wal_checkpoint_recovery(tmp_path):
    """WAL file survives crash and is replayed on next open.

    A crash before checkpoint leaves a -wal file. The next open should
    replay it automatically and produce a consistent database.
    """
    db_path = tmp_path / "state.db"
    root_path = str(Path(__file__).resolve().parents[2])
    db_path_str = str(db_path)

    child_code = f"""
import sys, os
sys.path.insert(0, {root_path!r})
from hermes_state import SessionDB
from pathlib import Path
db = SessionDB(Path({db_path_str!r}))
db.create_session("wal-test", source="cli")
db.append_messages_batch("wal-test", [
    {{"role": "user", "content": "wal checkpoint test message"}},
])
# Exit without checkpoint
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", child_code], capture_output=True, timeout=30)

    # WAL file should exist (or journal, depending on mode)
    # Either way, reopen should recover
    db = SessionDB(db_path)
    try:
        session = db.get_session("wal-test")
        assert session is not None, "session lost after WAL crash"
        messages = db.get_messages("wal-test")
        assert len(messages) >= 1, "messages lost after WAL crash"
    finally:
        db.close()


def test_concurrent_writers_no_corruption(tmp_path):
    """Multiple processes writing concurrently don't corrupt the database.

    Each child writes to its own session. After all finish, the DB should
    pass integrity_check and all sessions should be present.
    """
    db_path = tmp_path / "state.db"
    root_path = str(Path(__file__).resolve().parents[2])
    db_path_str = str(db_path)

    # Initialize the DB first
    db = SessionDB(db_path)
    db.close()

    procs = []
    for i in range(4):
        code = f"""
import sys, os
sys.path.insert(0, {root_path!r})
from hermes_state import SessionDB
from pathlib import Path
db = SessionDB(Path({db_path_str!r}))
db.create_session("worker-{i}", source="cli")
for j in range(5):
    db.append_messages_batch("worker-{i}", [
        {{"role": "user", "content": "worker-{i} message " + str(j)}},
    ])
db.close()
"""
        procs.append(subprocess.Popen([sys.executable, "-c", code]))

    for p in procs:
        p.wait(timeout=30)

    # Verify
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        assert _integrity_ok(conn), "integrity_check failed after concurrent writes"
        for i in range(4):
            row = conn.execute(
                "SELECT count(*) FROM sessions WHERE id = ?", (f"worker-{i}",)
            ).fetchone()
            assert row[0] == 1, f"worker-{i} session missing after concurrent writes"
    finally:
        conn.close()


def test_schema_version_survives_crash(tmp_path):
    """Schema version is persisted and survives a crash.

    If a migration bumps user_version but crashes before checkpoint,
    the next open should see the new version (WAL replay) and not re-run
    the migration.
    """
    db_path = tmp_path / "state.db"
    root_path = str(Path(__file__).resolve().parents[2])
    db_path_str = str(db_path)

    # Create and migrate
    db = SessionDB(db_path)
    db.close()

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    # Crash a child that reopens (no migration needed, just verify version)
    child_code = f"""
import sys, os
sys.path.insert(0, {root_path!r})
from hermes_state import SessionDB
from pathlib import Path
db = SessionDB(Path({db_path_str!r}))
db.create_session("version-test", source="cli")
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", child_code], capture_output=True, timeout=30)

    conn = sqlite3.connect(str(db_path))
    version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    assert version_after == version_before, (
        f"schema version changed after crash: {version_before} -> {version_after}"
    )
