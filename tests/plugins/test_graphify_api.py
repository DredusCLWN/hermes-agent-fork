"""Tests for the graphify dashboard plugin API."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Set HERMES_HOME to a temp directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes" / "graphify_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path / "hermes"


@pytest.fixture
def app(temp_hermes_home):
    """Create a FastAPI app with the graphify router mounted."""
    from plugins.graphify.dashboard.plugin_api import router

    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/graphify")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_status_no_graph(client, temp_hermes_home):
    """Status endpoint returns 'none' when no graph exists."""
    response = client.get("/api/plugins/graphify/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("none", "no_code")
    assert data["graph_exists"] is False


def test_status_with_graph(client, temp_hermes_home):
    """Status returns 'ready' when graph.json exists."""
    from plugins.graphify.dashboard.plugin_api import _graph_path, _save_metadata

    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "nodes": [{"id": "a", "type": "file"}, {"id": "b", "type": "file"}],
        "links": [{"source": "a", "target": "b"}],
    }))

    _save_metadata({
        "cwd": "/tmp/test",
        "cwd_hash": "abc123",
        "node_count": 2,
        "edge_count": 1,
        "community_count": 1,
        "completed_at": 1234567890,
    })

    response = client.get("/api/plugins/graphify/status")
    assert response.status_code == 200
    data = response.json()
    assert data["graph_exists"] is True
    assert data["node_count"] == 2
    assert data["edge_count"] == 1


def test_graph_json_not_found(client, temp_hermes_home):
    """GET /graph.json returns 404 when no graph exists."""
    response = client.get("/api/plugins/graphify/graph.json")
    assert response.status_code == 404


def test_graph_json_with_data(client, temp_hermes_home):
    """GET /graph.json returns graph data when it exists."""
    from plugins.graphify.dashboard.plugin_api import _graph_path

    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "nodes": [
            {"id": "a", "label": "module_a", "type": "file", "community": 0},
            {"id": "b", "label": "module_b", "type": "file", "community": 1},
        ],
        "links": [{"source": "a", "target": "b", "kind": "import"}],
    }))

    response = client.get("/api/plugins/graphify/graph.json")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1


def test_graph_json_with_limit(client, temp_hermes_home):
    """GET /graph.json?limit=1 returns only 1 node."""
    from plugins.graphify.dashboard.plugin_api import _graph_path

    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "nodes": [
            {"id": "a", "type": "file"},
            {"id": "b", "type": "file"},
            {"id": "c", "type": "file"},
        ],
        "links": [],
    }))

    response = client.get("/api/plugins/graphify/graph.json?limit=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1


def test_graph_json_node_filter(client, temp_hermes_home):
    """GET /graph.json?node=a returns subgraph around node a."""
    from plugins.graphify.dashboard.plugin_api import _graph_path

    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "nodes": [
            {"id": "a", "type": "file"},
            {"id": "b", "type": "file"},
            {"id": "c", "type": "file"},
        ],
        "links": [
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
        ],
    }))

    response = client.get("/api/plugins/graphify/graph.json?node=a")
    assert response.status_code == 200
    data = response.json()
    node_ids = {n["id"] for n in data["nodes"]}
    assert "a" in node_ids
    assert "b" in node_ids
    assert "c" not in node_ids


def test_nodes_endpoint(client, temp_hermes_home):
    """GET /nodes returns sorted node list."""
    from plugins.graphify.dashboard.plugin_api import _graph_path

    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "nodes": [
            {"id": "a", "degree_centrality": 0.8},
            {"id": "b", "degree_centrality": 0.3},
            {"id": "c", "degree_centrality": 0.5},
        ],
        "links": [],
    }))

    response = client.get("/api/plugins/graphify/nodes?sort=degree&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 2
    assert data["nodes"][0]["id"] == "a"
    assert data["nodes"][1]["id"] == "c"
    assert data["total"] == 3
