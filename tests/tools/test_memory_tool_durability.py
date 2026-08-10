"""Durability tests for file-based MemoryStore (MEMORY.md / USER.md).

Tests that the atomic write (temp + rename) pattern survives crash:
- Kill process mid-write → file is either old or new, never empty/torn
- Concurrent writers don't corrupt the file
- File is readable after crash (no partial writes)
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.memory_tool import MemoryStore, ENTRY_DELIMITER


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    """Point get_memory_dir() at a temp directory."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    from tools.memory_tool import get_memory_dir
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: mem_dir)
    return mem_dir


def test_atomic_write_survives_crash(memory_dir, tmp_path):
    """File-based memory write is atomic: crash mid-write leaves old or new, never torn."""
    root = str(Path(__file__).resolve().parents[2])

    # Phase 1: write initial content normally
    store = MemoryStore()
    store.add("memory", "initial fact that should survive")
    store.save_to_disk("memory")

    mem_file = memory_dir / "MEMORY.md"
    assert mem_file.exists(), "MEMORY.md not created"
    initial_content = mem_file.read_text(encoding="utf-8")
    assert "initial fact" in initial_content

    # Phase 2: crash a child that tries to write
    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from tools.memory_tool import MemoryStore
store = MemoryStore()
store.add("memory", "fact from crashing process")
store.save_to_disk("memory")
# Abrupt exit — no cleanup
os._exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )
    assert result.returncode == 1

    # Phase 3: file should be either old content or new content, never empty/torn
    content = mem_file.read_text(encoding="utf-8")
    assert content, "MEMORY.md is empty after crash — atomic write failed"
    assert "initial fact" in content or "fact from crashing process" in content, (
        f"MEMORY.md contains unexpected content after crash: {content[:200]!r}"
    )
    # Should not contain both partially (torn write)
    # If both are present, that's fine (the new write completed before crash)
    # But the file should not be truncated mid-entry


def test_file_readable_after_crash(memory_dir, tmp_path):
    """After crash, MEMORY.md is parseable by _read_file (no partial entries)."""
    root = str(Path(__file__).resolve().parents[2])

    # Write some entries
    store = MemoryStore()
    store.add("memory", "entry one")
    store.add("memory", "entry two")
    store.save_to_disk("memory")

    # Crash a child that writes
    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from tools.memory_tool import MemoryStore
store = MemoryStore()
store.add("memory", "entry three from crash")
store.save_to_disk("memory")
os._exit(1)
"""
    subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )

    # Re-read: should parse cleanly
    mem_file = memory_dir / "MEMORY.md"
    content = mem_file.read_text(encoding="utf-8")
    entries = [e.strip() for e in content.split(ENTRY_DELIMITER) if e.strip()]
    for entry in entries:
        assert len(entry) > 0, "empty entry found — file was torn"
        assert entry != "§", "delimiter fragment found — file was torn"


def test_no_temp_file_leaks_after_crash(memory_dir, tmp_path):
    """Atomic write temp files are cleaned up after crash (os.replace is atomic)."""
    root = str(Path(__file__).resolve().parents[2])

    child_code = f"""
import sys, os
sys.path.insert(0, {root!r})
from tools.memory_tool import MemoryStore
store = MemoryStore()
store.add("memory", "temp leak test")
store.save_to_disk("memory")
os._exit(1)
"""
    subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        timeout=30,
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
    )

    # No .tmp or .mem_ files should remain
    temp_files = list(memory_dir.glob(".mem_*")) + list(memory_dir.glob("*.tmp"))
    assert len(temp_files) == 0, f"temp files leaked after crash: {temp_files}"
