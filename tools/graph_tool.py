"""graph_query tool — query the codebase dependency graph.

Service-gated tool that lets the agent query the graph built by the graphify
plugin. The tool is only visible to the model when a graph index exists
(``check_fn`` gates on ``graphify_cache/graph.json``).

Four modes:
  - ``query``    — subgraph search by node name (fuzzy match on labels)
  - ``path``     — shortest path between two nodes
  - ``explain``  — explain a node's connections
  - ``list``     — list top hub nodes by degree (use this first to discover names)

The tool delegates to the graphify CLI (``python -m graphify``).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)


def _get_cache_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "graphify_cache"
    except Exception:
        return Path.home() / ".hermes" / "graphify_cache"


def _graph_path() -> Path:
    return _get_cache_dir() / "graph.json"


def _check_graph_available() -> bool:
    """Graph tool is visible only when an index exists."""
    return _graph_path().exists()


def _ensure_graphify() -> bool:
    """Ensure graphify is installed."""
    try:
        import graphify  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        from tools.lazy_deps import ensure
        ensure("tool.graphify")
        return True
    except Exception as exc:
        logger.warning("Failed to lazy-install graphify: %s", exc)
        return False


def _run_graphify_cli(args: list[str], timeout: int = 60) -> Dict[str, Any]:
    """Run graphify CLI and return result dict.

    graphify outputs plain text (not JSON) for query/explain/path/god-nodes.
    We return the raw text capped at 8000 chars so the model can read it.
    """
    if not _ensure_graphify():
        return {"error": "graphifyy package not installed. Run: pip install graphifyy"}

    # Resolve cwd from metadata so CLI runs in the project directory
    cwd: Optional[str] = None
    try:
        meta_path = _get_cache_dir() / "metadata.json"
        if meta_path.exists():
            import json as _json
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            cwd = meta.get("cwd")
    except Exception:
        pass

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "graphify"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "Command failed")[:500]
            return {"error": err}
        out = proc.stdout.strip()
        if not out:
            return {"result": "(no output)"}
        # graphify outputs plain text, not JSON — return as-is (capped)
        return {"result": out[:8000]}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"error": str(exc)[:500]}


def _handle_graph_query(args: Dict[str, Any], **kw: Any) -> str:
    """Handle graph_query tool calls."""
    query = args.get("query", "")
    mode = args.get("mode", "query")
    from_node = args.get("from_node", "")
    to_node = args.get("to_node", "")
    top_n = args.get("top_n", 20)

    graph = str(_graph_path())

    if mode == "list":
        result = _run_graphify_cli(["god-nodes", "--top", str(top_n), "--graph", graph])
    elif mode == "path":
        if not from_node or not to_node:
            return json.dumps({"error": "from_node and to_node are required for path mode"})
        result = _run_graphify_cli(["path", from_node, to_node, "--graph", graph])
    elif mode == "explain":
        if not query:
            return json.dumps({"error": "query parameter is required for explain mode"})
        result = _run_graphify_cli(["explain", query, "--graph", graph])
    else:  # query
        if not query:
            return json.dumps({"error": "query parameter is required for query mode"})
        result = _run_graphify_cli(["query", query, "--graph", graph])

    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_GRAPH_QUERY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "graph_query",
        "description": (
            "Query the codebase dependency graph built by graphify. "
            "Use mode='list' first to discover top hub nodes by name, "
            "then mode='query' to search by node name (fuzzy match), "
            "mode='path' to trace connections between two nodes, "
            "mode='explain' to get a node's full connection list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Node name or label to search for (required for query/explain modes)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["query", "path", "explain", "list"],
                    "default": "query",
                    "description": "'list' = top hub nodes, 'query' = search by name, 'path' = trace A→B, 'explain' = node detail",
                },
                "from_node": {
                    "type": "string",
                    "description": "Source node name (path mode only)",
                },
                "to_node": {
                    "type": "string",
                    "description": "Target node name (path mode only)",
                },
                "top_n": {
                    "type": "integer",
                    "default": 20,
                    "description": "How many top nodes to return (list mode only)",
                },
            },
            "required": [],
        },
    },
}


registry.register(
    name="graph_query",
    toolset="graphify",
    schema=_GRAPH_QUERY_SCHEMA,
    handler=lambda args, **kw: _handle_graph_query(args, **kw),
    check_fn=_check_graph_available,
    emoji="🕸️",
    description=(
        "Query the codebase dependency graph built by graphify. "
        "Search for components, trace dependencies, or explain node connections."
    ),
)
