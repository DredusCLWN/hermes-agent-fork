"""Refine snapshot — backup/restore for /refine changes.

When /refine runs, it may modify skills and memory.  This module captures
a snapshot of the affected files before the review fork writes, so the
user can roll back with /refine rollback if the changes are unwanted.

Snapshots are stored in ~/.hermes/refine_snapshots/<timestamp>/ and contain
copies of any skill files or MEMORY.md that existed before the review.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_SNAPSHOT_DIR_NAME = "refine_snapshots"
_MAX_SNAPSHOTS = 10


def _snapshot_root() -> Path:
    return get_hermes_home() / _SNAPSHOT_DIR_NAME


def _snapshot_dir(timestamp: Optional[float] = None) -> Path:
    ts = timestamp or time.time()
    return _snapshot_root() / f"{int(ts)}"


def capture_pre_refine_snapshot(
    skill_paths: Optional[List[Path]] = None,
    memory_paths: Optional[List[Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Capture a snapshot of skill/memory files before /refine runs.

    Returns a metadata dict with the snapshot path and file list, or None
    on failure.  Files that don't exist are skipped (not recorded).
    """
    try:
        snap_dir = _snapshot_dir()
        snap_dir.mkdir(parents=True, exist_ok=True)

        files_backed_up: List[str] = []
        all_paths = (skill_paths or []) + (memory_paths or [])

        for src in all_paths:
            if not src or not src.exists() or not src.is_file():
                continue
            rel = src.name if src.parent == get_hermes_home() else str(src)
            dst = snap_dir / rel.replace("/", "_").replace("\\", "_")
            shutil.copy2(src, dst)
            files_backed_up.append(str(src))

        if not files_backed_up:
            # Nothing to back up — remove the empty dir
            try:
                snap_dir.rmdir()
            except OSError:
                pass
            return None

        meta = {
            "timestamp": time.time(),
            "snapshot_dir": str(snap_dir),
            "files": files_backed_up,
        }
        (snap_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        _prune_old_snapshots()
        logger.debug("refine snapshot captured: %d files in %s",
                     len(files_backed_up), snap_dir)
        return meta
    except Exception:
        logger.debug("capture_pre_refine_snapshot failed", exc_info=True)
        return None


def list_snapshots() -> List[Dict[str, Any]]:
    """List available refine snapshots, newest first."""
    root = _snapshot_root()
    if not root.exists():
        return []
    snapshots = []
    for d in sorted(root.iterdir(), reverse=True):
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                snapshots.append(meta)
            except Exception:
                continue
    return snapshots[:_MAX_SNAPSHOTS]


def rollback_latest() -> Optional[Dict[str, Any]]:
    """Roll back the most recent refine snapshot.

    Restores all files from the snapshot.  Returns the metadata dict on
    success, None if no snapshots exist or restore failed.
    """
    snapshots = list_snapshots()
    if not snapshots:
        return None

    latest = snapshots[0]
    snap_dir = Path(latest["snapshot_dir"])
    if not snap_dir.exists():
        return None

    restored: List[str] = []
    for original_path_str in latest.get("files", []):
        original = Path(original_path_str)
        backup_name = original_path_str.replace("/", "_").replace("\\", "_")
        backup = snap_dir / backup_name
        if backup.exists():
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, original)
                restored.append(original_path_str)
            except Exception:
                logger.debug("rollback restore failed for %s", original_path_str, exc_info=True)

    logger.debug("refine rollback restored %d files", len(restored))
    return {"restored": restored, "snapshot": latest}


def _prune_old_snapshots() -> None:
    """Keep only the most recent _MAX_SNAPSHOTS snapshots."""
    root = _snapshot_root()
    if not root.exists():
        return
    dirs = sorted(root.iterdir(), reverse=True)
    for old in dirs[_MAX_SNAPSHOTS:]:
        try:
            shutil.rmtree(old)
        except Exception:
            pass
