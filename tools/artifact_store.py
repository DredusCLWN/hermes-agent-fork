"""Artifact store — persistent full tool output for out-of-the-box token savings.

When ``tool_output.artifact_store_enabled`` is True (default), the full output
of terminal commands and file reads is saved to ``~/.hermes/artifacts/`` so
nothing is lost when the model only sees a head+tail window. The model receives
a path reference it can use to retrieve the full output via ``read_file`` if
needed.

Layout::

    ~/.hermes/artifacts/
        2026-08-05/
            <uuid>.txt

Lazy cleanup runs at process startup (``cleanup_artifacts``):
  - Deletes files older than ``artifact_ttl_days`` (default 7).
  - Enforces a total size cap of ``artifact_max_gb`` (default 1 GB) by
    deleting the oldest files first.

This module NEVER raises in normal operation — a failure to save an artifact
is logged and returns ``None`` so the calling tool still works (just without
the artifact path).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CLEANUP_THRESHOLD_S = 3600  # min 1h between cleanup runs
_last_cleanup: float = 0.0


def _get_artifacts_base() -> Path:
    """Return the artifacts base directory under HERMES_HOME."""
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / "artifacts"


def save_artifact(tool_name: str, output: str) -> Optional[str]:
    """Save full tool output to the artifact store.

    Returns the absolute path string of the saved file, or ``None`` if the
    artifact store is disabled or the save failed. Never raises.
    """
    from tools.tool_output_limits import is_artifact_store_enabled

    if not is_artifact_store_enabled():
        return None

    if not output:
        return None

    try:
        base = _get_artifacts_base()
        date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        artifact_dir = base / date_dir
        artifact_dir.mkdir(parents=True, exist_ok=True)

        artifact_id = uuid.uuid4().hex[:12]
        filename = f"{tool_name}_{artifact_id}.txt"
        artifact_path = artifact_dir / filename

        artifact_path.write_text(output, encoding="utf-8", errors="replace")
        return str(artifact_path)
    except Exception as exc:
        logger.warning("artifact_store: failed to save %s output: %s", tool_name, exc)
        return None


def cleanup_artifacts() -> None:
    """Lazy cleanup — delete old artifacts and enforce size cap.

    Runs at most once per ``_CLEANUP_THRESHOLD_S`` to avoid repeated I/O on
    every process start. Never raises.
    """
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_THRESHOLD_S:
        return
    _last_cleanup = now

    try:
        from tools.tool_output_limits import get_artifact_ttl_days, get_artifact_max_gb

        ttl_days = get_artifact_ttl_days()
        max_gb = get_artifact_max_gb()
        base = _get_artifacts_base()

        if not base.exists():
            return

        cutoff = now - (ttl_days * 86400)

        # Phase 1: delete files older than TTL.
        for entry in base.rglob("*.txt"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                pass

        # Phase 2: enforce total size cap (oldest first).
        max_bytes = max_gb * 1024 * 1024 * 1024
        all_files = []
        total_size = 0
        for entry in base.rglob("*.txt"):
            try:
                size = entry.stat().st_size
                mtime = entry.stat().st_mtime
                all_files.append((mtime, entry, size))
                total_size += size
            except OSError:
                pass

        if total_size <= max_bytes:
            return

        all_files.sort(key=lambda x: x[0])
        for mtime, entry, size in all_files:
            if total_size <= max_bytes:
                break
            try:
                entry.unlink()
                total_size -= size
            except OSError:
                pass

        # Phase 3: remove empty date directories.
        for child in base.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                try:
                    child.rmdir()
                except OSError:
                    pass

    except Exception as exc:
        logger.warning("artifact_store: cleanup failed: %s", exc)


def truncate_with_head_tail(
    output: str,
    keep_first: int,
    keep_last: int,
) -> tuple[str, bool]:
    """Split output into head+tail window, returning (truncated_text, was_truncated).

    If the output has fewer lines than ``keep_first + keep_last``, it is
    returned unchanged with ``was_truncated=False``.
    """
    lines = output.splitlines(keepends=True)
    if len(lines) <= keep_first + keep_last:
        return output, False

    head = lines[:keep_first]
    tail = lines[-keep_last:]
    omitted = len(lines) - keep_first - keep_last
    separator = f"\n... [{omitted} lines omitted — full output in artifact store] ...\n"
    truncated = "".join(head) + separator + "".join(tail)
    return truncated, True
