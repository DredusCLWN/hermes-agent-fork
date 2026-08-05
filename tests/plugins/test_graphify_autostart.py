"""Tests for the graphify auto-start hook."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Set HERMES_HOME to a temp directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes" / "graphify_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path / "hermes"


def test_detect_code_files_with_python(tmp_path):
    """_detect_code_files returns True when .py files are present."""
    from plugins.graphify.dashboard.plugin_api import _detect_code_files

    (tmp_path / "main.py").write_text("print('hello')")
    assert _detect_code_files(tmp_path) is True


def test_detect_code_files_empty_dir(tmp_path):
    """_detect_code_files returns False for empty directory."""
    from plugins.graphify.dashboard.plugin_api import _detect_code_files

    assert _detect_code_files(tmp_path) is False


def test_detect_code_files_non_code_only(tmp_path):
    """_detect_code_files returns False when only non-code files exist."""
    from plugins.graphify.dashboard.plugin_api import _detect_code_files

    (tmp_path / "README.md").write_text("# Test")
    (tmp_path / "config.yaml").write_text("key: value")
    assert _detect_code_files(tmp_path) is False


def test_detect_code_files_nested_src(tmp_path):
    """_detect_code_files finds code in src/ subdirectory."""
    from plugins.graphify.dashboard.plugin_api import _detect_code_files

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("console.log('hi')")
    assert _detect_code_files(tmp_path) is True


def test_needs_reindex_no_metadata(temp_hermes_home):
    """_needs_reindex returns True when no metadata exists."""
    from plugins.graphify.dashboard.plugin_api import _needs_reindex

    assert _needs_reindex("/tmp/test_project") is True


def test_needs_reindex_different_cwd(temp_hermes_home):
    """_needs_reindex returns True when cwd hash differs."""
    from plugins.graphify.dashboard.plugin_api import _needs_reindex, _save_metadata

    _save_metadata({
        "cwd": "/tmp/project_a",
        "cwd_hash": "different_hash",
        "node_count": 10,
        "edge_count": 5,
        "completed_at": 9999999999,
    })

    assert _needs_reindex("/tmp/project_b") is True


def test_needs_reindex_graph_missing(temp_hermes_home):
    """_needs_reindex returns True when graph.json is missing despite metadata."""
    from plugins.graphify.dashboard.plugin_api import _needs_reindex, _save_metadata, _cwd_hash

    cwd = "/tmp/test_project"
    _save_metadata({
        "cwd": cwd,
        "cwd_hash": _cwd_hash(cwd),
        "node_count": 10,
        "completed_at": 9999999999,
    })

    assert _needs_reindex(cwd) is True


def test_maybe_auto_start_no_code(tmp_path, temp_hermes_home):
    """maybe_auto_start sets status to 'no_code' when no code files found."""
    from plugins.graphify.dashboard.plugin_api import maybe_auto_start, _build_state

    maybe_auto_start(str(tmp_path))
    assert _build_state["status"] == "no_code"


def test_maybe_auto_start_with_code(tmp_path, temp_hermes_home):
    """maybe_auto_start triggers build when code files are found."""
    from plugins.graphify.dashboard.plugin_api import maybe_auto_start, _build_state

    (tmp_path / "main.py").write_text("print('hello')")

    with patch("plugins.graphify.dashboard.plugin_api.start_build") as mock_build:
        mock_build.return_value = "test_task"
        maybe_auto_start(str(tmp_path))
        mock_build.assert_called_once_with(str(tmp_path))


def test_on_session_start_hook(tmp_path, temp_hermes_home, monkeypatch):
    """The on_session_start hook calls maybe_auto_start with cwd."""
    from plugins.graphify import _on_session_start

    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("print('hello')")

    with patch("plugins.graphify.dashboard.plugin_api.maybe_auto_start") as mock_start:
        _on_session_start(cwd=str(tmp_path), session_id="test")
        mock_start.assert_called_once_with(str(tmp_path))


def test_on_session_start_hook_no_code(tmp_path, temp_hermes_home, monkeypatch):
    """The on_session_start hook doesn't fail when no code files present."""
    from plugins.graphify import _on_session_start

    # Should not raise even with empty dir
    _on_session_start(cwd=str(tmp_path), session_id="test")
