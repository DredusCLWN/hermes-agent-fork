"""Tests for tools.artifact_store.

Covers:
1. save_artifact writes file and returns path.
2. save_artifact returns None when disabled.
3. save_artifact returns None for empty output.
4. truncate_with_head_tail splits correctly.
5. cleanup_artifacts deletes old files.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.artifact_store import (
    save_artifact,
    cleanup_artifacts,
    truncate_with_head_tail,
)


@pytest.fixture
def _temp_artifacts(tmp_path, monkeypatch):
    """Redirect artifacts base to a temp directory."""
    base = tmp_path / "artifacts"
    base.mkdir()
    monkeypatch.setattr(
        "tools.artifact_store._get_artifacts_base",
        lambda: base,
    )
    # Reset cleanup throttle.
    import tools.artifact_store as asm
    monkeypatch.setattr(asm, "_last_cleanup", 0.0)
    return base


class TestSaveArtifact:
    def test_save_returns_path_when_enabled(self, _temp_artifacts):
        with patch("tools.tool_output_limits.is_artifact_store_enabled", return_value=True):
            path = save_artifact("terminal", "hello world\n")
        assert path is not None
        assert os.path.exists(path)
        assert "terminal" in os.path.basename(path)

    def test_save_returns_none_when_disabled(self, _temp_artifacts):
        with patch("tools.tool_output_limits.is_artifact_store_enabled", return_value=False):
            path = save_artifact("terminal", "hello world\n")
        assert path is None

    def test_save_returns_none_for_empty_output(self, _temp_artifacts):
        with patch("tools.tool_output_limits.is_artifact_store_enabled", return_value=True):
            path = save_artifact("terminal", "")
        assert path is None

    def test_saved_content_matches_input(self, _temp_artifacts):
        content = "line1\nline2\nline3\n"
        with patch("tools.tool_output_limits.is_artifact_store_enabled", return_value=True):
            path = save_artifact("terminal", content)
        assert path is not None
        assert Path(path).read_text(encoding="utf-8") == content


class TestTruncateWithHeadTail:
    def test_short_output_not_truncated(self):
        output = "line1\nline2\nline3\n"
        result, truncated = truncate_with_head_tail(output, 50, 80)
        assert result == output
        assert truncated is False

    def test_long_output_truncated(self):
        lines = [f"line {i}\n" for i in range(200)]
        output = "".join(lines)
        result, truncated = truncate_with_head_tail(output, 10, 20)
        assert truncated is True
        assert "omitted" in result
        # Head should contain first 10 lines.
        assert "line 0\n" in result
        assert "line 9\n" in result
        # Tail should contain last 20 lines.
        assert "line 199\n" in result
        assert "line 180\n" in result
        # Middle should be omitted.
        assert "line 50\n" not in result


class TestCleanupArtifacts:
    def test_cleanup_deletes_old_files(self, _temp_artifacts):
        base = _temp_artifacts
        # Create an old file.
        old_file = base / "old.txt"
        old_file.write_text("old content")
        old_time = time.time() - (30 * 86400)  # 30 days ago
        os.utime(old_file, (old_time, old_time))

        with patch("tools.tool_output_limits.get_artifact_ttl_days", return_value=7), \
             patch("tools.tool_output_limits.get_artifact_max_gb", return_value=1):
            cleanup_artifacts()

        assert not old_file.exists()

    def test_cleanup_keeps_recent_files(self, _temp_artifacts):
        base = _temp_artifacts
        recent_file = base / "recent.txt"
        recent_file.write_text("recent content")

        with patch("tools.tool_output_limits.get_artifact_ttl_days", return_value=7), \
             patch("tools.tool_output_limits.get_artifact_max_gb", return_value=1):
            cleanup_artifacts()

        assert recent_file.exists()
