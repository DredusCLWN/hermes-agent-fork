"""Graphify dashboard plugin — backend API routes.

Mounted at /api/plugins/graphify/ by the dashboard plugin system.

Provides REST endpoints for building, querying, and visualizing a codebase
dependency graph. The graph is built using ``graphify`` (tree-sitter AST,
local, no LLM) which is lazy-installed on first use.

An optional ``/enhance`` endpoint walks the existing AST graph and uses a
local LLM (Ollama) to infer additional relationships between components.

Auto-start: the ``on_session_start`` hook in the plugin ``__init__.py``
scans cwd for code files and kicks off a background indexing thread when
code is detected. The agent can query the graph via the ``graph_query``
service-gated tool (tools/graph_tool.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CODE_EXTENSIONS = {
    ".py", ".ts", ".mts", ".cts", ".js", ".jsx", ".tsx", ".mjs",
    ".go", ".rs", ".java", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".rb", ".cs", ".kt", ".scala", ".php", ".swift", ".lua", ".luau",
    ".ps1", ".psm1", ".psd1", ".ex", ".exs", ".m", ".mm", ".jl",
    ".zig", ".dart", ".v", ".sv", ".svh",
    ".sql", ".f95", ".f03", ".f08", ".pas", ".pp",
    ".sh", ".bash", ".json",
    ".sln", ".csproj", ".fsproj", ".vbproj",
    ".xaml", ".razor", ".cshtml",
    ".astro", ".groovy", ".gradle",
}

_BUILD_TIMEOUT_SECONDS = 600  # 10 minutes max for very large repos

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _get_cache_dir() -> Path:
    """Return the graphify cache directory under HERMES_HOME."""
    try:
        from hermes_constants import get_hermes_home
        cache = get_hermes_home() / "graphify_cache"
    except Exception:
        cache = Path.home() / ".hermes" / "graphify_cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _graph_path() -> Path:
    return _get_cache_dir() / "graph.json"


def _metadata_path() -> Path:
    return _get_cache_dir() / "metadata.json"


def _cwd_hash(cwd: str) -> str:
    return hashlib.sha256(cwd.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Build state (in-process tracking)
# ---------------------------------------------------------------------------

_build_state: Dict[str, Any] = {
    "status": "none",  # none, building, ready, stale, error, no_code
    "progress": 0.0,
    "error": None,
    "task_id": None,
    "started_at": None,
    "completed_at": None,
}
_build_lock = threading.Lock()
_build_proc: Optional[subprocess.Popen] = None


def _load_metadata() -> Dict[str, Any]:
    """Load metadata.json from cache, or empty dict."""
    path = _metadata_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_metadata(data: Dict[str, Any]) -> None:
    """Save metadata.json atomically."""
    path = _metadata_path()
    try:
        from utils import atomic_json_write
        atomic_json_write(path, data, indent=2)
    except Exception:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build", "target", ".git", ".hg", ".svn", "Library", "Temp", "Logs"}


def _detect_code_files(cwd: Path) -> bool:
    """Return True if cwd contains at least one code file (scan up to 3 levels deep)."""
    try:
        for entry in sorted(cwd.iterdir()):
            if entry.is_file() and entry.suffix.lower() in _CODE_EXTENSIONS:
                return True
            # Check up to 3 levels deep for source directories
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in _SKIP_DIRS:
                try:
                    for child in entry.iterdir():
                        if child.is_file() and child.suffix.lower() in _CODE_EXTENSIONS:
                            return True
                        if child.is_dir() and not child.name.startswith(".") and child.name not in _SKIP_DIRS:
                            for grandchild in child.iterdir():
                                if grandchild.is_file() and grandchild.suffix.lower() in _CODE_EXTENSIONS:
                                    return True
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        pass
    return False


def _needs_reindex(cwd: str) -> bool:
    """Check if re-indexing is needed based on metadata and git diff."""
    meta = _load_metadata()
    if not meta:
        return True

    # Check cwd hash — different project needs fresh index
    if meta.get("cwd_hash") != _cwd_hash(cwd):
        return True

    # Check if graph.json exists
    if not _graph_path().exists():
        return True

    # Check staleness via git diff
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            changed = result.stdout.strip().split("\n")
            code_changed = any(
                Path(f).suffix.lower() in _CODE_EXTENSIONS for f in changed
            )
            if code_changed:
                return True
        elif result.returncode != 0:
            # git error (shallow clone with no HEAD~1, etc.) — mtime fallback
            last_build = meta.get("completed_at", 0)
            if time.time() - last_build > 3600:  # 1 hour
                return True
    except (OSError, subprocess.SubprocessError, FileNotFoundError):
        # No git or git error — check mtime staleness
        last_build = meta.get("completed_at", 0)
        if time.time() - last_build > 3600:  # 1 hour
            return True

    return False


def _ensure_graphify() -> bool:
    """Ensure graphify is installed. Returns True if available."""
    try:
        import graphify  # noqa: F401
        return True
    except ImportError:
        pass

    # Try lazy install
    try:
        from tools.lazy_deps import ensure, FeatureUnavailable
        ensure("tool.graphify")
        return True
    except Exception as exc:
        log.warning("Failed to lazy-install graphify: %s", exc)
        return False


def _run_build(cwd: str, task_id: str, mode: str = "ast", backend: str = "", model: str = "") -> None:
    """Background thread: run graphify extraction.

    mode:
      "ast"      — tree-sitter only, no LLM (fast, local)
      "semantic" — AST + LLM semantic extraction (deeper, needs API key)
    backend: gemini|kimi|claude|openai|deepseek|ollama (semantic mode)
    model: override backend default model (semantic mode)
    """
    global _build_state, _build_proc

    try:
        if not _ensure_graphify():
            with _build_lock:
                _build_state.update({
                    "status": "error",
                    "error": "graphify package not installed. Run: pip install graphify",
                    "task_id": None,
                })
            return

        with _build_lock:
            _build_state.update({
                "status": "building",
                "progress": 0.0,
                "task_id": task_id,
                "started_at": time.time(),
                "error": None,
                "mode": mode,
                "backend": backend or None,
                "model": model or None,
            })

        output_dir = _get_cache_dir() / "graphify_out"
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [sys.executable, "-m", "graphify", "extract", ".",
               "--out", str(output_dir)]

        if mode == "ast":
            cmd.append("--code-only")
        else:
            # Semantic mode — AST + LLM
            if backend:
                cmd.extend(["--backend", backend])
            if model:
                cmd.extend(["--model", model])

        log.info("graphify build: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            # Read only graph.json at the end; never let stdout accumulate in
            # a pipe. The old code streamed stderr alone then read stdout, so
            # graphify writing >~64KB to stdout would fill the buffer, block
            # the child, and hold this thread in "building" forever.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        with _build_lock:
            _build_proc = proc

        # Stream stderr in a helper thread (progress + warnings) while this
        # thread waits with a hard timeout. The old single-threaded loop had
        # NO timeout of its own, so a child that just blocked mid-write (no
        # stderr, no exit) never got reaped either.
        warning_lines: list[str] = []
        _stop_reader = threading.Event()

        def _stderr_reader() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                if _stop_reader.is_set():
                    break
                line_s = line.rstrip()
                if not line_s:
                    continue
                if line_s.strip().startswith("[graphify]"):
                    warning_lines.append(line_s)
                    # Try to parse progress from "chunk N/M" patterns
                    m = re.search(r'chunk\s+(\d+)\s*/\s*(\d+)', line_s, re.IGNORECASE)
                    if m:
                        cur_chunk = int(m.group(1))
                        total_chunks = int(m.group(2))
                        pct = min(90.0, (cur_chunk / max(total_chunks, 1)) * 90.0)
                        with _build_lock:
                            _build_state["progress"] = pct
                    m2 = re.search(r'(\d+)\s*/\s*(\d+)\s+dispatched', line_s, re.IGNORECASE)
                    if m2:
                        cur_file = int(m2.group(1))
                        total_files = int(m2.group(2))
                        pct = min(90.0, (cur_file / max(total_files, 1)) * 90.0)
                        with _build_lock:
                            _build_state["progress"] = pct

        _reader = threading.Thread(target=_stderr_reader, name="graphify-stderr", daemon=True)
        _reader.start()
        try:
            proc.wait(timeout=_BUILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            _stop_reader.set()
            _reader.join(timeout=2)
            with _build_lock:
                _build_proc = None
            raise
        _stop_reader.set()
        _reader.join(timeout=2)
        returncode = proc.returncode
        with _build_lock:
            _build_proc = None

        build_warnings = "\n".join(warning_lines[:10])
        if build_warnings:
            log.warning("graphify build warnings:\n%s", build_warnings)

        if returncode != 0:
            # Read stderr already consumed; use warning_lines as error
            error_text = build_warnings or f"Build failed (exit {returncode})"
            with _build_lock:
                _build_state.update({
                    "status": "error",
                    "error": error_text[:500],
                    "task_id": None,
                })
            return

        # graphify writes graph.json to <output>/graphify-out/graph.json
        source_graph = output_dir / "graphify-out" / "graph.json"
        if not source_graph.exists():
            # Try direct <output>/graph.json
            source_graph = output_dir / "graph.json"

        if source_graph.exists():
            # Copy to cache
            import shutil
            shutil.copy2(source_graph, _graph_path())
            # Clean up temp output directory
            try:
                shutil.rmtree(output_dir, ignore_errors=True)
            except Exception:
                pass
            with _build_lock:
                _build_state["progress"] = 95.0
        else:
            with _build_lock:
                _build_state.update({
                    "status": "error",
                    "error": "graph.json not found after build",
                    "task_id": None,
                })
            return

        # Load graph to count nodes/edges
        node_count = 0
        edge_count = 0
        community_count = 0
        try:
            graph_data = json.loads(_graph_path().read_text(encoding="utf-8"))
            node_count = len(graph_data.get("nodes", []))
            edge_count = len(graph_data.get("links", graph_data.get("edges", [])))
            communities = set()
            for node in graph_data.get("nodes", []):
                comm = node.get("community") or node.get("cluster")
                if comm is not None:
                    communities.add(comm)
            community_count = len(communities)
        except (OSError, ValueError):
            pass

        # If semantic build produced 0 nodes, treat as error — likely
        # the LLM backend failed (model corrupted, API key missing, etc.)
        if node_count == 0:
            error_msg = "Build produced 0 nodes"
            if build_warnings:
                # Extract the most useful warning line
                for line in build_warnings.splitlines():
                    if "failed" in line.lower() or "error" in line.lower():
                        error_msg = line.strip()
                        break
                else:
                    error_msg = build_warnings.splitlines()[0].strip()
            with _build_lock:
                _build_state.update({
                    "status": "error",
                    "error": error_msg[:500],
                    "task_id": None,
                })
            return

        _save_metadata({
            "cwd": cwd,
            "cwd_hash": _cwd_hash(cwd),
            "node_count": node_count,
            "edge_count": edge_count,
            "community_count": community_count,
            "completed_at": time.time(),
        })

        with _build_lock:
            _build_state.update({
                "status": "ready",
                "progress": 100.0,
                "task_id": None,
                "completed_at": time.time(),
                "warnings": build_warnings or None,
            })

    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        with _build_lock:
            _build_state.update({
                "status": "error",
                "error": f"Build timed out after {_BUILD_TIMEOUT_SECONDS}s",
                "task_id": None,
            })
    except Exception as exc:
        log.exception("Graphify build failed")
        with _build_lock:
            _build_state.update({
                "status": "error",
                "error": str(exc)[:500],
                "task_id": None,
            })


def start_build(
    cwd: Optional[str] = None,
    mode: str = "ast",
    backend: str = "",
    model: str = "",
) -> str:
    """Start a background build. Returns task_id.

    mode: "ast" (tree-sitter only) or "semantic" (AST + LLM)
    backend: LLM backend for semantic mode (ollama, openai, claude, ...)
    model: override backend default model
    """
    if cwd is None:
        cwd = os.getcwd()

    with _build_lock:
        if _build_state.get("status") == "building":
            return _build_state.get("task_id") or "existing"

        task_id = f"build_{int(time.time() * 1000)}"
        thread = threading.Thread(
            target=_run_build,
            args=(cwd, task_id, mode, backend, model),
            name="graphify-build",
            daemon=True,
        )
        thread.start()
        return task_id


def maybe_auto_start(cwd: Optional[str] = None) -> None:
    """Check cwd for code files and auto-start indexing if needed.

    Called from on_session_start hook. Non-blocking.
    """
    if cwd is None:
        cwd = os.getcwd()

    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        return

    if not _detect_code_files(cwd_path):
        with _build_lock:
            _build_state["status"] = "no_code"
        return

    if not _needs_reindex(cwd):
        with _build_lock:
            if _build_state["status"] not in ("building", "error"):
                _build_state["status"] = "ready"
        return

    start_build(cwd)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------


class BuildResponse(BaseModel):
    task_id: str
    status: str


class BuildRequest(BaseModel):
    mode: str = "ast"  # "ast" (tree-sitter only, default) or "semantic" (AST + LLM)
    backend: str = ""   # ollama, openai, claude, deepseek, gemini, kimi
    model: str = ""     # override backend default model
    cwd: str = ""       # override working directory (project path)


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """Return current index status and metadata."""
    with _build_lock:
        status = dict(_build_state)

    meta = _load_metadata()
    graph_exists = _graph_path().exists()

    return {
        "status": status.get("status", "none"),
        "progress": status.get("progress", 0.0),
        "error": status.get("error"),
        "started_at": status.get("started_at"),
        "completed_at": status.get("completed_at"),
        "graph_exists": graph_exists,
        "node_count": meta.get("node_count", 0),
        "edge_count": meta.get("edge_count", 0),
        "community_count": meta.get("community_count", 0),
        "cwd": meta.get("cwd"),
        "mode": status.get("mode", "ast"),
        "backend": status.get("backend"),
        "model": status.get("model"),
        "warnings": status.get("warnings"),
    }


@router.post("/build", response_model=BuildResponse)
async def build_graph(body: BuildRequest = BuildRequest()) -> BuildResponse:
    """Start background indexing of cwd.

    Body:
      mode: "ast" (tree-sitter only, default) or "semantic" (AST + LLM)
      backend: LLM backend for semantic mode (ollama|openai|claude|deepseek|gemini|kimi)
      model: override backend default model name
    """
    task_id = start_build(
        cwd=body.cwd or None,
        mode=body.mode,
        backend=body.backend,
        model=body.model,
    )
    return BuildResponse(task_id=task_id, status="building")


@router.post("/cancel")
async def cancel_build() -> Dict[str, Any]:
    """Cancel a running build by killing the subprocess."""
    with _build_lock:
        proc = _build_proc
        current_status = _build_state.get("status")
        if current_status != "building" or proc is None:
            return {"status": "not_building"}
        _build_proc = None

    try:
        proc.kill()
    except Exception as exc:
        log.warning("Failed to kill build process: %s", exc)

    with _build_lock:
        _build_state.update({
            "status": "none",
            "progress": 0.0,
            "error": None,
            "task_id": None,
        })

    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# LLM Enhancement — walk existing AST graph and infer deeper connections
# ---------------------------------------------------------------------------

def _ollama_chat(model: str, prompt: str, timeout: int = 120) -> Optional[str]:
    """Call local Ollama API and return the response text, or None on error."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
    except Exception as exc:
        log.warning("Ollama chat failed: %s", exc)
        return None


def _parse_llm_edges(text: str, id_to_label: Dict[str, str]) -> List[Dict[str, Any]]:
    """Parse LLM response lines into edge dicts.

    Expected format: SOURCE_LABEL -> RELATION -> TARGET_LABEL
    """
    edges = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Try to parse: source -> relation -> target
        parts = line.split("->")
        if len(parts) < 3:
            continue
        src_label = parts[0].strip()
        relation = parts[1].strip().lower().replace(" ", "_")
        tgt_label = parts[-1].strip()
        # Match labels to node IDs (fuzzy)
        src_id = _fuzzy_match_node(src_label, id_to_label)
        tgt_id = _fuzzy_match_node(tgt_label, id_to_label)
        if src_id and tgt_id and src_id != tgt_id:
            edges.append({
                "source": src_id,
                "target": tgt_id,
                "relation": relation,
                "confidence": "INFERRED",
                "_origin": "llm",
                "weight": 0.5,
                "confidence_score": 0.5,
            })
    return edges


def _fuzzy_match_node(label: str, id_to_label: Dict[str, str]) -> Optional[str]:
    """Match a label to a node ID by case-insensitive matching.

    Exact match first, then prefix match. Substring match is avoided
    because it produces false positives like `main` → `main_main`.
    """
    label_lower = label.lower().strip("()")
    # 1. Exact match
    for nid, nlabel in id_to_label.items():
        if label_lower == nlabel.lower().strip("()"):
            return nid
    # 2. Exact match on the ID itself
    for nid in id_to_label:
        if label_lower == nid.lower():
            return nid
    # 3. Prefix match (label is a prefix of node label or vice versa)
    for nid, nlabel in id_to_label.items():
        nlabel_lower = nlabel.lower().strip("()")
        if nlabel_lower.startswith(label_lower) or label_lower.startswith(nlabel_lower):
            # Reject if one is just the other repeated (e.g. main vs main_main)
            if nlabel_lower.replace(label_lower, "").strip("_") == "":
                continue
            return nid
    return None


def _run_enhance(model: str, task_id: str) -> None:
    """Background thread: enhance existing graph with LLM-inferred edges."""
    try:
        graph_file = _graph_path()
        if not graph_file.exists():
            with _build_lock:
                _build_state.update({
                    "status": "error",
                    "error": "No graph.json found. Build AST graph first.",
                    "task_id": None,
                })
            return

        with _build_lock:
            _build_state.update({
                "status": "enhancing",
                "progress": 0.0,
                "task_id": task_id,
                "started_at": time.time(),
                "error": None,
                "mode": "enhance",
                "backend": "ollama",
                "model": model,
            })

        # Load existing graph
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        nodes = data.get("nodes", [])
        links = data.get("links", [])

        id_to_label = {n["id"]: n.get("label", n["id"]) for n in nodes}

        # Group nodes by community for relevant batching
        communities: Dict[int, List[Dict]] = {}
        for n in nodes:
            comm = n.get("community", 0)
            communities.setdefault(comm, []).append(n)

        # Sort communities by size (process largest first for impact)
        sorted_comms = sorted(communities.items(), key=lambda x: -len(x[1]))

        # Process top communities (cap at 50 to keep it reasonable)
        communities_to_process = sorted_comms[:50]
        total_batches = len(communities_to_process)
        new_edges: List[Dict[str, Any]] = []

        existing_pairs = {
            (l.get("source"), l.get("target"), l.get("relation"))
            for l in links
        }

        # Reserve last 10% of progress for community labeling
        edge_progress_end = 80

        for i, (comm_id, comm_nodes) in enumerate(communities_to_process):
            with _build_lock:
                _build_state["progress"] = (i / total_batches) * edge_progress_end

            # Cap batch size to fit LLM context
            batch = comm_nodes[:30]
            node_descriptions = "\n".join(
                f"- {n.get('label', n['id'])} ({n.get('source_file', '?')})"
                for n in batch
            )

            prompt = (
                "You are a code dependency analyzer. Given these code components "
                "from the same module cluster, identify additional relationships "
                "that are NOT obvious from imports/calls alone — such as logical "
                "dependencies, shared patterns, or conceptual connections.\n\n"
                f"Components (cluster {comm_id}):\n{node_descriptions}\n\n"
                "For each relationship, output ONE line in this exact format:\n"
                "SOURCE -> RELATION -> TARGET\n\n"
                "Rules:\n"
                "- SOURCE and TARGET must be DIFFERENT components from the list above\n"
                "- Do NOT connect a component to itself or to a near-duplicate\n"
                "- Only output relationships that are meaningful and specific\n\n"
                "Relations: depends_on, similar_to, validates, configures, "
                "dispatches_to, transforms\n\n"
                "Output ONLY relationship lines, no explanations."
            )

            response = _ollama_chat(model, prompt, timeout=120)
            if response:
                batch_edges = _parse_llm_edges(response, id_to_label)
                # Deduplicate against existing edges
                for e in batch_edges:
                    key = (e["source"], e["target"], e["relation"])
                    if key not in existing_pairs:
                        existing_pairs.add(key)
                        new_edges.append(e)

            log.info("enhance batch %d/%d: +%d edges (total new: %d)",
                     i + 1, total_batches, len(batch_edges), len(new_edges))

        # ── Community labeling ──────────────────────────────────────────
        # Ask LLM to name each community based on its member components.
        community_labels: Dict[int, str] = {}
        label_comms = sorted_comms[:80]  # label top 80 communities
        total_labels = len(label_comms)

        for i, (comm_id, comm_nodes) in enumerate(label_comms):
            with _build_lock:
                _build_state["progress"] = edge_progress_end + (
                    (i / max(total_labels, 1)) * (100 - edge_progress_end)
                )

            sample = comm_nodes[:15]
            member_names = ", ".join(
                n.get("label", n["id"]) for n in sample
            )
            label_prompt = (
                "You are a code architect. Given these code components from the same "
                "module cluster, give the cluster a short descriptive name (2-4 words).\n\n"
                f"Components: {member_names}\n\n"
                "Output ONLY the name, nothing else."
            )
            resp = _ollama_chat(model, label_prompt, timeout=60)
            if resp:
                name = resp.strip().splitlines()[0].strip().strip('"').strip("'")
                if name and len(name) <= 60:
                    community_labels[comm_id] = name

            log.info("community label %d/%d: cluster %d → %s",
                     i + 1, total_labels, comm_id, community_labels.get(comm_id, "?"))

        # Apply labels to nodes
        if community_labels:
            for n in nodes:
                comm = n.get("community")
                if comm in community_labels:
                    n["community_label"] = community_labels[comm]
            data["nodes"] = nodes
            data["community_labels"] = community_labels

        # Merge new edges into graph and save
        if new_edges or community_labels:
            links.extend(new_edges)
            data["links"] = links
            data["enhanced"] = True
            data["enhanced_at"] = time.time()
            data["enhanced_model"] = model
            data["enhanced_edges_added"] = len(new_edges)
            data["community_labels"] = community_labels

            from utils import atomic_write_text
            atomic_write_text(graph_file, json.dumps(data, indent=0), encoding="utf-8")

            # Update metadata
            meta = _load_metadata()
            meta["edge_count"] = len(links)
            meta["enhanced"] = True
            meta["enhanced_edges_added"] = len(new_edges)
            meta["community_labels"] = len(community_labels)
            _save_metadata(meta)

        with _build_lock:
            _build_state.update({
                "status": "ready",
                "progress": 100.0,
                "task_id": None,
                "completed_at": time.time(),
                "enhanced_edges": len(new_edges),
            })

        log.info("graph enhance complete: +%d edges", len(new_edges))

    except Exception as exc:
        log.exception("Graph enhance failed")
        with _build_lock:
            _build_state.update({
                "status": "error",
                "error": str(exc)[:500],
                "task_id": None,
            })


class EnhanceRequest(BaseModel):
    model: str = ""   # Ollama model name
    cwd: str = ""     # unused but kept for API consistency


@router.post("/enhance", response_model=BuildResponse)
async def enhance_graph(body: EnhanceRequest = EnhanceRequest()) -> BuildResponse:
    """Enhance existing AST graph with LLM-inferred relationships.

    Walks the existing graph.json, groups nodes by community, and asks
    a local Ollama model to identify additional connections. Does NOT
    rebuild the graph — only adds new edges with _origin='llm'.
    """
    model = body.model or "qwen2.5-coder:7b"

    with _build_lock:
        if _build_state.get("status") in ("building", "enhancing"):
            return BuildResponse(task_id="existing", status=_build_state["status"])

        if not _graph_path().exists():
            raise HTTPException(status_code=404, detail="No graph found. Build AST graph first.")

        task_id = f"enhance_{int(time.time() * 1000)}"
        thread = threading.Thread(
            target=_run_enhance,
            args=(model, task_id),
            name="graphify-enhance",
            daemon=True,
        )
        thread.start()
        return BuildResponse(task_id=task_id, status="enhancing")


@router.get("/graph.json")
async def get_graph(
    filter: Optional[str] = Query(None, description="Filter: community name or node type"),
    node: Optional[str] = Query(None, description="Get subgraph around this node"),
    limit: Optional[int] = Query(None, description="Limit number of nodes"),
) -> Dict[str, Any]:
    """Return the dependency graph in node-link format."""
    if not _graph_path().exists():
        raise HTTPException(status_code=404, detail="Graph not built. Call POST /build first.")

    try:
        data = json.loads(_graph_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read graph: {exc}")

    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))

    # Filter by community
    if filter and filter != "all":
        filtered_nodes = [
            n for n in nodes
            if str(n.get("community") or n.get("cluster") or "") == filter
            or n.get("type") == filter
        ]
        node_ids = {n.get("id") for n in filtered_nodes}
        filtered_links = [
            e for e in links
            if e.get("source") in node_ids or e.get("target") in node_ids
        ]
        return {"nodes": filtered_nodes, "links": filtered_links}

    # Subgraph around a node
    if node:
        node_ids = {node}
        related_links = [
            e for e in links
            if e.get("source") == node or e.get("target") == node
        ]
        for e in related_links:
            node_ids.add(e.get("source"))
            node_ids.add(e.get("target"))
        filtered_nodes = [n for n in nodes if n.get("id") in node_ids]
        return {"nodes": filtered_nodes, "links": related_links}

    # Limit
    if limit and limit > 0 and len(nodes) > limit:
        nodes = nodes[:limit]
        node_ids = {n.get("id") for n in nodes}
        links = [e for e in links if e.get("source") in node_ids and e.get("target") in node_ids]

    return {"nodes": nodes, "links": links}


@router.get("/query")
async def query_graph(q: str = Query(..., description="Natural language query or node name")) -> Dict[str, Any]:
    """Query the graph using graphify query CLI."""
    if not _graph_path().exists():
        raise HTTPException(status_code=404, detail="Graph not built.")

    if not _ensure_graphify():
        raise HTTPException(status_code=503, detail="graphify not installed")

    try:
        output_dir = _get_cache_dir() / "graphify_out"
        proc = subprocess.run(
            [sys.executable, "-m", "graphify", "query", q,
             "--graph", str(_graph_path())],
            cwd=str(output_dir) if output_dir.exists() else os.getcwd(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return {"query": q, "error": proc.stderr[:500] if proc.stderr else "Query failed", "results": []}
        try:
            results = json.loads(proc.stdout)
            return {"query": q, "results": results}
        except ValueError:
            return {"query": q, "results": [], "raw": proc.stdout[:2000]}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Query timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/path")
async def find_path(
    from_node: str = Query(..., alias="from"),
    to_node: str = Query(..., alias="to"),
) -> Dict[str, Any]:
    """Find shortest path between two nodes."""
    if not _graph_path().exists():
        raise HTTPException(status_code=404, detail="Graph not built.")

    if not _ensure_graphify():
        raise HTTPException(status_code=503, detail="graphify not installed")

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "graphify", "path", from_node, to_node,
             "--graph", str(_graph_path())],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return {"from": from_node, "to": to_node, "error": proc.stderr[:500] if proc.stderr else "Path not found", "path": []}
        try:
            result = json.loads(proc.stdout)
            return {"from": from_node, "to": to_node, "path": result}
        except ValueError:
            return {"from": from_node, "to": to_node, "path": [], "raw": proc.stdout[:2000]}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Path query timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/nodes")
async def get_nodes(
    sort: str = Query("degree", description="Sort by: degree, betweenness, closeness"),
    limit: int = Query(50, description="Max nodes to return"),
) -> Dict[str, Any]:
    """Return node list with metadata, sorted by centrality."""
    if not _graph_path().exists():
        raise HTTPException(status_code=404, detail="Graph not built.")

    try:
        data = json.loads(_graph_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read graph: {exc}")

    nodes = data.get("nodes", [])

    # Sort by centrality metric
    sort_key = f"{sort}_centrality"
    nodes_sorted = sorted(
        nodes,
        key=lambda n: n.get(sort_key, n.get(sort, 0)),
        reverse=True,
    )

    if limit > 0:
        nodes_sorted = nodes_sorted[:limit]

    return {"nodes": nodes_sorted, "sort": sort, "total": len(nodes)}


@router.post("/invalidate")
async def invalidate() -> Dict[str, str]:
    """Force re-index on next build."""
    meta = _load_metadata()
    meta["force_reindex"] = True
    _save_metadata(meta)

    # Trigger a fresh build using the cwd from metadata
    start_build(cwd=meta.get("cwd") or None)

    return {"status": "reindexing"}


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

_CURATED_MODELS: Dict[str, List[str]] = {
    "openai": [
        "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
        "o3", "o3-mini", "o4-mini",
    ],
    "claude": [
        "claude-opus-4-20250514", "claude-sonnet-4-20250514",
        "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ],
    "deepseek": [
        "deepseek-chat", "deepseek-coder", "deepseek-reasoner",
    ],
    "gemini": [
        "gemini-2.5-flash", "gemini-2.5-pro",
        "gemini-2.0-flash", "gemini-2.0-flash-lite",
    ],
    "kimi": [
        "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
    ],
}


@router.get("/models")
async def get_models(backend: str = Query(..., description="LLM backend name")) -> Dict[str, Any]:
    """Return available models for a given backend.

    For ollama: queries the local Ollama API (localhost:11434).
    For others: returns a curated list of common models.
    """
    backend = backend.lower()

    if backend == "ollama":
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                return {"backend": "ollama", "models": models, "live": True}
        except Exception as exc:
            log.warning("Failed to query Ollama models: %s", exc)
            return {
                "backend": "ollama",
                "models": [],
                "live": False,
                "error": "Ollama not reachable (is it running?)",
            }

    curated = _CURATED_MODELS.get(backend, [])
    return {"backend": backend, "models": curated, "live": False}


# ---------------------------------------------------------------------------
# Plugin registration (on_session_start hook)
# ---------------------------------------------------------------------------

# This module is imported by the plugin loader when the plugin is enabled.
# The on_session_start hook is registered via plugin.yaml + __init__.py.

# When imported as a plugin module, register the hook:
try:
    from hermes_constants import get_hermes_home as _get_hermes_home  # noqa: F401
except Exception:
    pass
