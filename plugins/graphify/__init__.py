"""graphify plugin — auto-build and query codebase dependency graphs.

Wires one behaviour:

1. ``on_session_start`` hook — scans cwd for code files and kicks off
   a background indexing thread when code is detected. Non-blocking;
   the agent starts immediately while the graph builds in parallel.

The dashboard plugin (plugins/graphify/dashboard/) provides the REST API
and visual /graph page. The agent tool (tools/graph_tool.py) provides
the ``graph_query`` service-gated tool.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _on_session_start(
    cwd: str = "",
    session_id: str = "",
    **_: Any,
) -> None:
    """Auto-start graph indexing when code files are detected in cwd."""
    try:
        from plugins.graphify.dashboard.plugin_api import maybe_auto_start

        work_dir = cwd or os.getcwd()
        maybe_auto_start(work_dir)
    except Exception as exc:
        logger.debug("graphify auto-start failed: %s", exc)


def register(ctx) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
