"""Tests for the graph_query tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Set HERMES_HOME to a temp directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    cache_dir = tmp_path / "hermes" / "graphify_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def test_check_graph_available_false(temp_hermes_home):
    """check_fn returns False when no graph.json exists."""
    from tools.graph_tool import _check_graph_available

    assert _check_graph_available() is False


def test_check_graph_available_true(temp_hermes_home):
    """check_fn returns True when graph.json exists."""
    from tools.graph_tool import _check_graph_available, _graph_path

    graph_path = _graph_path()
    graph_path.write_text(json.dumps({"nodes": [], "links": []}))

    assert _check_graph_available() is True


def test_handle_graph_query_no_query(temp_hermes_home):
    """Handler returns error when query is empty."""
    from tools.graph_tool import _handle_graph_query

    result = _handle_graph_query({"query": "", "mode": "query"})
    data = json.loads(result)
    assert "error" in data


def test_handle_graph_query_path_mode_missing_nodes(temp_hermes_home):
    """Handler returns error for path mode without from/to."""
    from tools.graph_tool import _handle_graph_query

    result = _handle_graph_query({"query": "", "mode": "path"})
    data = json.loads(result)
    assert "error" in data
    assert "from_node" in data["error"]


def test_handle_graph_query_path_mode(temp_hermes_home):
    """Handler delegates to graphifyy CLI for path mode."""
    from tools.graph_tool import _handle_graph_query

    with patch("tools.graph_tool._run_graphify_cli") as mock_cli:
        mock_cli.return_value = {"path": ["a", "b", "c"]}
        result = _handle_graph_query({
            "query": "",
            "mode": "path",
            "from_node": "a",
            "to_node": "c",
        })
        data = json.loads(result)
        assert data["path"] == ["a", "b", "c"]
        mock_cli.assert_called_once()


def test_handle_graph_query_query_mode(temp_hermes_home):
    """Handler delegates to graphifyy CLI for query mode."""
    from tools.graph_tool import _handle_graph_query

    with patch("tools.graph_tool._run_graphify_cli") as mock_cli:
        mock_cli.return_value = {"results": [{"id": "a", "type": "file"}]}
        result = _handle_graph_query({"query": "main entry", "mode": "query"})
        data = json.loads(result)
        assert "results" in data
        mock_cli.assert_called_once()


def test_graph_query_registered():
    """graph_query tool is registered in the registry."""
    from tools.registry import registry

    tool = registry.get("graph_query")
    assert tool is not None
    assert tool.toolset == "graphify"


def test_graph_query_schema():
    """graph_query schema has required fields."""
    from tools.registry import registry

    tool = registry.get("graph_query")
    assert tool is not None
    schema = tool.schema
    assert schema["function"]["name"] == "graph_query"
    assert "query" in schema["function"]["parameters"]["properties"]
    assert "mode" in schema["function"]["parameters"]["properties"]
    assert "query" in schema["function"]["parameters"]["required"]
