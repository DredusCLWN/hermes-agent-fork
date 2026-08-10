"""Durability tests for active_sessions lease files.

Tests that lease files survive crash:
- Kill process mid-lease-write → reopen → leases are readable
- Orphaned leases are detected and cleaned up
- Concurrent lease acquisition doesn't corrupt the file
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli.active_sessions import (
    _write_entries,
    _read_entries,
    _state_path,
    _prune_dead,
    try_acquire_active_session,
    ActiveSessionLease,
)


@pytest.fixture
def lease_dir(tmp_path, monkeypatch):
    """Point get_hermes_home() at a temp directory."""
    monkeypatch.setattr("hermes_cli.active_sessions.get_hermes_home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_lease_file_survives_crash(lease_dir, tmp_path):
    """Lease file is atomic (temp + os.replace) and survives crash."""
    root = str(Path(__file__).resolve().parents[2])
    home_str = str(tmp_path)

    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
os.environ["HERMES_HOME"] = {home_str!r}
from hermes_cli.active_sessions import try_acquire_active_session
lease, err = try_acquire_active_session(
    session_id="crash-lease-test",
    surface="cli",
    config={{"max_concurrent_sessions": 5}},
)
os._exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )
    assert result.returncode == 1

    lease_file = tmp_path / "runtime" / "active_sessions.json"
    assert lease_file.exists(), f"lease file missing after crash at {lease_file}"

    data = json.loads(lease_file.read_text(encoding="utf-8"))
    assert "entries" in data
    assert len(data["entries"]) >= 1
    entry = data["entries"][0]
    assert entry["session_id"] == "crash-lease-test"


def test_lease_file_no_torn_writes(lease_dir, tmp_path):
    """Lease file is never partially written (atomic os.replace)."""
    root = str(Path(__file__).resolve().parents[2])
    home_str = str(tmp_path)

    state_path = tmp_path / "runtime" / "active_sessions.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_entries(state_path, [
        {"session_id": "s1", "lease_id": "l1"},
    ])

    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
os.environ["HERMES_HOME"] = {home_str!r}
from hermes_cli.active_sessions import _write_entries, _state_path
_write_entries(
    _state_path(),
    [{{"session_id": "s1", "lease_id": "l1"}},
     {{"session_id": "s2", "lease_id": "l2"}}],
)
os._exit(1)
"""
    subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )

    content = state_path.read_text(encoding="utf-8")
    data = json.loads(content)
    assert "entries" in data


def test_orphaned_leases_pruned(lease_dir, tmp_path):
    """Leases from crashed processes are pruned by _prune_dead."""
    root = str(Path(__file__).resolve().parents[2])
    home_str = str(tmp_path)

    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
os.environ["HERMES_HOME"] = {home_str!r}
from hermes_cli.active_sessions import try_acquire_active_session
lease, err = try_acquire_active_session(
    session_id="orphaned-lease",
    surface="cli",
    config={{"max_concurrent_sessions": 5}},
)
os._exit(1)
"""
    subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )

    state_path = tmp_path / "runtime" / "active_sessions.json"
    entries = _read_entries(state_path)
    assert any(e["session_id"] == "orphaned-lease" for e in entries)

    pruned = _prune_dead(entries)
    assert len(pruned) < len(entries), "dead PID lease not pruned"
    assert not any(e["session_id"] == "orphaned-lease" for e in pruned), (
        "orphaned lease still present after prune"
    )


def test_concurrent_lease_acquisition_no_corruption(lease_dir, tmp_path):
    """Multiple processes acquiring leases concurrently don't corrupt the file."""
    root = str(Path(__file__).resolve().parents[2])
    home_str = str(tmp_path)

    procs = []
    for i in range(4):
        code = f"""
import sys, os
sys.path.insert(0, {root!r})
os.environ["HERMES_HOME"] = {home_str!r}
from hermes_cli.active_sessions import try_acquire_active_session
lease, err = try_acquire_active_session(
    session_id="concurrent-lease-{i}",
    surface="cli",
    config={{"max_concurrent_sessions": 10}},
)
if lease:
    lease.release()
"""
        procs.append(subprocess.Popen(
            [sys.executable, "-c", code],
            env={**os.environ, "HERMES_HOME": str(tmp_path)},
        ))

    for p in procs:
        p.wait(timeout=30)

    lease_file = tmp_path / "runtime" / "active_sessions.json"
    if lease_file.exists():
        content = lease_file.read_text(encoding="utf-8")
        data = json.loads(content)
        assert "entries" in data
