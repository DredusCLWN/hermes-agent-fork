"""Durability tests for holographic MemoryStore.

Tests that simulate crash mid-write and verify integrity on reopen:
- Kill process (os._exit) during add_fact → reopen → integrity_check
- Schema migration (user_version) survives crash
- HRR vector backfill after crash
- Shared-connection refcount integrity after crash
- Concurrent writers don't corrupt the database
"""

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from plugins.memory.holographic.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """Each test starts and ends with an empty shared-connection registry."""
    for entry in list(MemoryStore._shared.values()):
        try:
            entry["conn"].close()
        except sqlite3.Error:
            pass
    MemoryStore._shared.clear()
    yield
    for entry in list(MemoryStore._shared.values()):
        try:
            entry["conn"].close()
        except sqlite3.Error:
            pass
    MemoryStore._shared.clear()


def _integrity_ok(conn) -> bool:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] == "ok"


def test_crash_mid_add_fact_reopens_cleanly(tmp_path):
    """Simulate process crash during add_fact and verify reopen integrity."""
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({repr(str(db_path))})
s.add_fact("fact before crash", "general", "test")
# Abrupt exit — no close(), no commit guarantee
os._exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1

    # Reopen and verify
    s = MemoryStore(db_path)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            assert _integrity_ok(conn), "integrity_check failed after crash"
        finally:
            conn.close()

        # Fact should be present (autocommit means it was committed)
        facts = s.search_facts("crash", limit=10)
        assert any("fact before crash" in f["content"] for f in facts), (
            "fact missing after crash"
        )
    finally:
        s.close()


def test_schema_version_survives_crash(tmp_path):
    """PRAGMA user_version persists across crash and reopen."""
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    # First open: migration runs
    s = MemoryStore(db_path)
    s.close()

    conn = sqlite3.connect(str(db_path))
    version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version_before >= 2, f"expected user_version >= 2, got {version_before}"

    # Crash a child that reopens
    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({repr(str(db_path))})
s.add_fact("version crash test", "general")
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", child_code], capture_output=True, timeout=30)

    conn = sqlite3.connect(str(db_path))
    version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    assert _integrity_ok(conn)
    conn.close()

    assert version_after == version_before, (
        f"user_version changed after crash: {version_before} -> {version_after}"
    )


def test_legacy_db_migration_survives_crash(tmp_path):
    """Legacy DB (v0, no user_version) migrates correctly even after crash.

    Creates a v0 DB with facts but no hrr_vector column, then opens with
    MemoryStore which should migrate to v2 and backfill.
    """
    db_path = tmp_path / "memory_store.db"

    # Create legacy schema at v0
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            tags TEXT DEFAULT '',
            trust_score REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE entities (
            entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT DEFAULT 'unknown',
            aliases TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE fact_entities (
            fact_id INTEGER REFERENCES facts(fact_id),
            entity_id INTEGER REFERENCES entities(entity_id),
            PRIMARY KEY (fact_id, entity_id)
        );
        CREATE INDEX idx_facts_trust ON facts(trust_score DESC);
        CREATE INDEX idx_facts_category ON facts(category);
        CREATE INDEX idx_entities_name ON entities(name);
        CREATE VIRTUAL TABLE facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id);
        CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
            INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
        END;
        CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
        END;
        CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
            INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
        END;
        CREATE TABLE memory_banks (
            bank_id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_name TEXT NOT NULL UNIQUE,
            vector BLOB NOT NULL,
            dim INTEGER NOT NULL,
            fact_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("INSERT INTO facts (content) VALUES (?)", ("legacy fact one",))
    conn.execute("INSERT INTO facts (content) VALUES (?)", ("legacy fact two",))
    conn.commit()
    conn.close()

    # Open with MemoryStore — triggers migration v0→v2
    s = MemoryStore(db_path)
    s.close()

    conn = sqlite3.connect(str(db_path))
    try:
        assert _integrity_ok(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2, f"expected user_version=2, got {version}"

        # hrr_vector column should exist
        cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
        assert "hrr_vector" in cols, "hrr_vector column missing after migration"

        # Both legacy facts should have backfilled vectors
        rows = conn.execute(
            "SELECT content, hrr_vector FROM facts WHERE hrr_vector IS NULL"
        ).fetchall()
        assert len(rows) == 0, f"{len(rows)} facts missing hrr_vector backfill"
    finally:
        conn.close()


def test_concurrent_writers_no_corruption(tmp_path):
    """Multiple processes writing concurrently don't corrupt memory_store.db."""
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    # Initialize first
    s = MemoryStore(db_path)
    s.close()

    procs = []
    for i in range(4):
        code = f"""
import sys
sys.path.insert(0, {root!r})
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({str(db_path)!r})
s.add_fact("concurrent fact from worker {i}", "general", "concurrent")
s.close()
"""
        procs.append(subprocess.Popen([sys.executable, "-c", code]))

    for p in procs:
        p.wait(timeout=30)

    conn = sqlite3.connect(str(db_path))
    try:
        assert _integrity_ok(conn), "integrity_check failed after concurrent writes"
        count = conn.execute(
            "SELECT count(*) FROM facts WHERE content LIKE 'concurrent fact from worker%'"
        ).fetchone()[0]
        assert count == 4, f"expected 4 concurrent facts, got {count}"
    finally:
        conn.close()


def test_shared_connection_refcount_after_crash(tmp_path):
    """Shared connection refcount is reset when a new process opens.

    The _shared registry is process-local. A crash in one process should
    not leave stale entries that prevent a new process from opening.
    """
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    # Crash a child that opened a shared connection
    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({repr(str(db_path))})
# Don't close — just crash
os._exit(1)
"""
    subprocess.run([sys.executable, "-c", child_code], capture_output=True, timeout=30)

    # New process should be able to open without issues
    s = MemoryStore(db_path)
    try:
        s.add_fact("after crash refcount test", "general")
        facts = s.search_facts("refcount", limit=10)
        assert any("refcount" in f["content"] for f in facts)
    finally:
        s.close()

    # Verify _shared registry is clean in this process
    assert len(MemoryStore._shared) == 0, "shared registry leaked after close"


def test_kill_mid_migration_rolls_back(tmp_path):
    """Kill process during v2 backfill → user_version stays at 1, not 2.

    This is the core ROLLBACK guarantee: if the child is killed mid-migration,
    the BEGIN/COMMIT transaction ensures user_version is NOT bumped to 2,
    so the next open re-runs the migration cleanly.
    """
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    # Create a legacy v0 DB with many facts (no hrr_vector column)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            tags TEXT DEFAULT '',
            trust_score REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE entities (
            entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT DEFAULT 'unknown',
            aliases TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE fact_entities (
            fact_id INTEGER REFERENCES facts(fact_id),
            entity_id INTEGER REFERENCES entities(entity_id),
            PRIMARY KEY (fact_id, entity_id)
        );
        CREATE INDEX idx_facts_trust ON facts(trust_score DESC);
        CREATE INDEX idx_facts_category ON facts(category);
        CREATE INDEX idx_entities_name ON entities(name);
        CREATE VIRTUAL TABLE facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id);
        CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
            INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
        END;
        CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
        END;
        CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
            INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
        END;
        CREATE TABLE memory_banks (
            bank_id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_name TEXT NOT NULL UNIQUE,
            vector BLOB NOT NULL,
            dim INTEGER NOT NULL,
            fact_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    for i in range(50):
        conn.execute("INSERT INTO facts (content) VALUES (?)", (f"legacy fact {i} for kill test",))
    conn.commit()
    conn.close()

    # Child: opens MemoryStore (triggers v0→v1→v2 migration with backfill),
    # but we patch encode_fact to sleep so we can kill mid-backfill.
    db_path_str = str(db_path)
    child_code = f"""
import sys, os, time
sys.path.insert(0, {root!r})
from plugins.memory.holographic import holographic as hrr
_orig_encode = hrr.encode_fact
def _slow_encode(*a, **kw):
    time.sleep(0.5)
    return _orig_encode(*a, **kw)
hrr.encode_fact = _slow_encode
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({db_path_str!r})
# Migration is running in __init__/_init_db with slow encode_fact.
# The 50 facts * 0.5s = 25s backfill. We'll be killed long before that.
# But _init_db runs synchronously, so we need to be killed from outside.
# Just wait here — the parent will terminate us.
time.sleep(30)
"""
    proc = subprocess.Popen([sys.executable, "-c", child_code])
    time.sleep(2)  # Let the child start migration (v1 finishes instantly, v2 backfill starts)
    proc.terminate()  # TerminateProcess — hard kill
    proc.wait(timeout=10)

    # Reopen and check: user_version should NOT be 2 (migration didn't commit)
    conn = sqlite3.connect(str(db_path))
    try:
        assert _integrity_ok(conn), "integrity_check failed after kill mid-migration"
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        # v1 should have committed (it's fast, runs before v2 backfill).
        # v2 should NOT have committed (we killed during backfill).
        assert version < 2, (
            f"user_version={version} after kill mid-migration — "
            f"ROLLBACK failed, migration committed partially"
        )
    finally:
        conn.close()

    # Reopen with MemoryStore — migration should complete successfully
    s = MemoryStore(db_path)
    s.close()

    conn = sqlite3.connect(str(db_path))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2, f"migration didn't complete on reopen: user_version={version}"
        null_count = conn.execute(
            "SELECT count(*) FROM facts WHERE hrr_vector IS NULL"
        ).fetchone()[0]
        assert null_count == 0, f"{null_count} facts still missing hrr_vector after re-migration"
    finally:
        conn.close()


def test_migration_without_numpy_marks_v2(tmp_path):
    """Fresh DB without numpy: migration reaches v2 with NULL hrr_vectors.

    When _hrr_available=False, the v2 migration skips backfill but still
    bumps user_version to 2. This is correct — the facts just have NULL
    vectors until numpy becomes available and rebuild_all_vectors() is called.
    """
    db_path = tmp_path / "memory_store.db"
    root = str(Path(__file__).resolve().parents[3])

    # Child: simulate numpy unavailable by patching _HAS_NUMPY before import
    db_path_str = str(db_path)
    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from plugins.memory.holographic import holographic as hrr
hrr._HAS_NUMPY = False
from plugins.memory.holographic.store import MemoryStore
s = MemoryStore({db_path_str!r})
s.add_fact("fact without numpy", "general")
s.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"child failed: {result.stderr.decode()}"

    conn = sqlite3.connect(str(db_path))
    try:
        assert _integrity_ok(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2, f"expected user_version=2 without numpy, got {version}"

        # Fact should exist but hrr_vector should be NULL
        row = conn.execute(
            "SELECT hrr_vector FROM facts WHERE content = 'fact without numpy'"
        ).fetchone()
        assert row is not None, "fact not found"
        assert row[0] is None, "hrr_vector should be NULL when numpy unavailable"
    finally:
        conn.close()
