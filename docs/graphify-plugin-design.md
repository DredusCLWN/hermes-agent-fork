# Graphify Plugin — Design Spec

**Date:** 2026-08-05
**Status:** Approved
**Approach:** A — Bundled plugin (Kanban pattern)

## Overview

Graphify is a bundled dashboard plugin that automatically builds and visualizes
a dependency graph of the codebase in the working directory. It follows the
Kanban plugin pattern: a `plugins/<name>/dashboard/` directory with a
`manifest.json`, a FastAPI `plugin_api.py` backend, and a compiled React
frontend (`dist/`).

The graph is built using [graphifyy](https://github.com/Graphify-Labs/graphify)
(tree-sitter AST, local, no LLM). The agent can query the graph via a
service-gated `graph_query` tool; the user can explore it visually on the
`/graph` dashboard page.

## Goals

- **Auto-start**: graph indexing begins automatically when ≥1 code file is
  detected in cwd at session start — no manual activation needed.
- **Visual graph**: interactive `/graph` page in the web dashboard with
  vis-network — nodes colored by community, sized by centrality, clickable
  inspection, path finder.
- **Agent tool**: `graph_query` service-gated tool lets the agent query
  dependencies without reading 100 files.
- **Narrow waist**: no new core tools in the model schema by default —
  `check_fn` gates visibility on index availability.
- **Cache-safe**: toolset composition does not change mid-conversation;
  `check_fn` TTL (30s) handles index availability drift.

## Non-goals (YAGNI)

- No real-time WebSocket graph updates (polling `/status` is sufficient).
- No TUI visualization (web dashboard only).
- No MCP server for the graph (tool + REST is enough).
- No multi-language semantic analysis beyond tree-sitter AST.

## Plugin Structure

```
plugins/graphify/
  dashboard/
    manifest.json          # tab: /graph, position: after:sessions
    plugin_api.py          # FastAPI router → /api/plugins/graphify/*
    dist/
      index.js             # React page with vis-network
      style.css
  __init__.py
  plugin.yaml              # hooks: on_session_start
```

### manifest.json

```json
{
  "name": "graphify",
  "label": "Code Graph",
  "description": "Visual dependency graph of the codebase — auto-built, interactive",
  "icon": "Code",
  "version": "1.0.0",
  "tab": { "path": "/graph", "position": "after:sessions" },
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

Registered as `source: "bundled"` (ships with the repo). Enabled by default —
visible in the sidebar immediately after Sessions. Can be disabled via
`hermes tools` or the Plugins page.

### plugin.yaml

```yaml
name: graphify
version: 1.0.0
description: "Auto-build and query codebase dependency graphs"
hooks:
  - on_session_start
```

## Backend (plugin_api.py)

FastAPI `APIRouter` mounted at `/api/plugins/graphify/`.

### REST Endpoints

| Endpoint   | Method | Description |
|------------|--------|-------------|
| `/status`  | GET    | Index status: `building` / `ready` / `stale` / `none` / `error`. Includes node count, edge count, community count, last build time, progress % |
| `/build`   | POST   | Start background indexing of cwd. Returns `task_id` for polling. Idempotent — if already building, returns existing task |
| `/graph.json` | GET | Download graph (NetworkX node-link format). Query params: `?filter=community`, `?node=<name>`, `?limit=N` |
| `/query`   | GET    | `?q=<text>` — natural language subgraph query. Delegates to `graphify query` |
| `/path`    | GET    | `?from=<A>&to=<B>` — shortest path between two nodes |
| `/nodes`   | GET    | Node list with metadata (type, centrality, community). For god-node ranking |
| `/invalidate` | POST | Force re-index (git diff check) |

### Lazy Install

`graphifyy` is installed on first `/build` via `tools/lazy_deps.py`:

```python
def _ensure_graphify():
    try:
        import graphifyy  # noqa
        return True
    except ImportError:
        return _lazy_install("graphifyy")
```

### Cache

- Location: `get_hermes_home() / "graphify_cache/"`
- Files: `graph.json` (NetworkX node-link), `metadata.json` (mtime, node count, edge count, build time, cwd hash)
- Build lock: `graphify_cache/build.lock` — only one concurrent build

### Auto-start (on_session_start hook)

1. Scan cwd for code files (tree-sitter extensions: `.py .ts .js .go .rs .java .c .cpp .rb .cs .kt .scala .php .swift .lua .ps1 .ex .jl` etc.)
2. If ≥1 file found → check `graphify_cache/metadata.json`
3. If cache missing or `git diff --name-only` shows changes → spawn background thread running `graphify . --code-only`
4. Non-blocking — agent starts immediately, indexing runs in parallel

### Invalidation

- Git repo: `git diff --name-only HEAD~1` → re-index changed files
- Non-git: full re-index if metadata older than 1 hour
- Manual: POST `/invalidate` → force re-index

## Frontend (React /graph page)

### Technology

- **vis-network** (`vis-network/standalone`) — interactive directed graph in canvas
- Lightweight, no d3 dependency

### Components

1. **GraphCanvas** — main canvas with vis-network
   - Nodes colored by community (Leiden clustering)
   - Node size proportional to degree centrality
   - Edges: arrows for directed (imports/calls), labels `EXTRACTED`/`INFERRED`
   - Pan, zoom, drag nodes

2. **GraphToolbar** — filters
   - By node type (file/function/class/module)
   - By community (dropdown of clusters)
   - Search by node name
   - Path finder: `from → to` inputs

3. **NodeInspector** — right panel on node click
   - Name, type, file, centrality metrics
   - Incoming/outgoing edges list
   - Clickable links → navigate to connected node

4. **StatusBar** — bottom bar
   - Index status (building/ready/stale)
   - Node/edge/community counts
   - "Rebuild" button → POST `/build`
   - Progress bar during indexing (poll `/status` every 3s)

5. **GodNodesPanel** — top-10 nodes by degree centrality

### Data Flow

```
GET /api/plugins/graphify/graph.json → vis-network DataSet → canvas
GET /api/plugins/graphify/status     → StatusBar (poll while building)
GET /api/plugins/graphify/path       → highlight path in canvas
POST /api/plugins/graphify/build     → trigger re-index
```

### Desktop Plugin

Parallel to the web dashboard plugin, a desktop plugin at
`apps/desktop/src/plugins/graphify/plugin.tsx` registers the `/graph` route
via `@hermes/plugin-sdk` (same pattern as `apps/desktop/src/plugins/kanban/plugin.tsx`):

- `ROUTES_AREA` — `/graph` route
- `SIDEBAR_NAV_AREA` — nav item with `Code` icon
- `PALETTE_AREA` — "Code Graph: Open" command

## Service-gated Tool (graph_query)

### Registration

```python
# tools/graph_tool.py
registry.register(
    name="graph_query",
    toolset="graphify",
    schema={
        "type": "function",
        "function": {
            "name": "graph_query",
            "description": "Query the codebase dependency graph. Returns nodes and edges matching the query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query or node name"},
                    "mode": {"type": "string", "enum": ["query", "path", "explain"], "default": "query"},
                    "from_node": {"type": "string", "description": "For path mode: source node"},
                    "to_node": {"type": "string", "description": "For path mode: target node"},
                },
                "required": ["query"],
            },
        },
    },
    handler=lambda args, **kw: _handle_graph_query(args, **kw),
    check_fn=_check_graph_available,
    emoji="🕸️",
)
```

### check_fn

```python
def _check_graph_available() -> bool:
    """Graph tool is visible only when an index exists."""
    cache = get_hermes_home() / "graphify_cache" / "graph.json"
    return cache.exists()
```

TTL-cached via existing registry mechanism (30s). If index is deleted or not
yet built, tool is invisible to the model.

### Toolset Integration

- `"graph_query"` added to `_HERMES_CORE_TOOLS` in `toolsets.py`
- Toolset `"graphify"` defined in `TOOLSETS` dict
- **Enabled by default** — `check_fn` handles visibility
- For `coder` preset: always included in schema (if index exists)
- For other presets: `check_fn` returns False if no index → tool not in schema

### Cache Safety

- Toolset composition does not change mid-conversation
- `check_fn` TTL (30s) handles index availability drift
- System prompt is byte-stable — no mid-conversation mutations

## Error Handling

- **graphifyy not installed**: lazy install on first `/build`. If install
  fails → `/status` returns `{"status": "error", "message": "..."}`,
  dashboard shows "Install" button
- **Indexing failed**: background thread logs error, `/status` → `error`,
  dashboard offers retry
- **Empty cwd (no code files)**: auto-start skips, `/status` → `{"status": "no_code"}`
- **Large repo (100K+ files)**: `graphify . --code-only` may take minutes.
  Progress poll via `/status` with percentage

## Edge Cases

- **Multi-profile**: cache in `get_hermes_home()` — shared across profiles.
  Index is tied to cwd, not profile
- **Concurrent builds**: `build.lock` file — only one indexing at a time
- **Git not installed**: mtime comparison fallback for invalidation
- **Non-git project**: full re-index at `on_session_start` if metadata > 1h old

## Testing

- `tests/plugins/test_graphify_api.py` — REST endpoints (build, status,
  graph.json, query, path) against temp HERMES_HOME
- `tests/plugins/test_graphify_autostart.py` — hook fires on code files,
  skips empty cwd
- `tests/tools/test_graph_tool.py` — `check_fn` gating, handler, schema
- E2E: temp project with 3 `.py` files → build → query → verify nodes/edges

## Files to Create/Modify

### New files

- `plugins/graphify/__init__.py`
- `plugins/graphify/plugin.yaml`
- `plugins/graphify/dashboard/manifest.json`
- `plugins/graphify/dashboard/plugin_api.py`
- `plugins/graphify/dashboard/dist/index.js` (built from TS source)
- `plugins/graphify/dashboard/dist/style.css`
- `tools/graph_tool.py`
- `apps/desktop/src/plugins/graphify/plugin.tsx` (desktop plugin)
- `apps/desktop/src/plugins/graphify/graph-canvas.tsx`
- `apps/desktop/src/plugins/graphify/api.ts`
- `tests/plugins/test_graphify_api.py`
- `tests/plugins/test_graphify_autostart.py`
- `tests/tools/test_graph_tool.py`

### Modified files

- `toolsets.py` — add `"graph_query"` to `_HERMES_CORE_TOOLS`, add `"graphify"` toolset
- `hermes_cli/config_defaults.py` — add graphify to `coder` preset description
- `model_tools.py` — import `tools.graph_tool` (auto-discovery handles this)

## Implementation Order

1. Backend: `plugin_api.py` + lazy install + auto-start hook
2. Tool: `tools/graph_tool.py` + toolset registration
3. Frontend: React `/graph` page with vis-network
4. Desktop plugin: `apps/desktop/src/plugins/graphify/`
5. Tests: API, autostart, tool
6. Integration: config defaults, preset updates
