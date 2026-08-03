"""Shared ffmpeg executable discovery for Discord media paths.

Looks for an explicit ``FFMPEG_PATH`` override, then ``shutil.which("ffmpeg")``,
then a common Windows winget fallback for installs that never touch PATH.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _shared_find_ffmpeg():
    """Return the first ffmpeg binary found on PATH, if any."""
    return shutil.which("ffmpeg")


def resolve_ffmpeg_executable() -> str:
    """Return an ffmpeg command that also covers common Windows installs."""
    explicit = os.getenv("FFMPEG_PATH")
    if explicit and explicit.strip():
        return os.path.expandvars(os.path.expanduser(explicit.strip()))

    discovered = _shared_find_ffmpeg()
    if discovered:
        return discovered

    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        packages_dir = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        candidates = sorted(packages_dir.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"))
        if candidates:
            return str(candidates[-1])

    return "ffmpeg"
