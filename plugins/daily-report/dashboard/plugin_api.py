"""Daily Report plugin — backend API.

Ships bundled; mounted by the dashboard web server under
``/api/plugins/daily-report/``. Exposes the pieces the desktop SDK plugin
can't reach client-side: the profile's long-term memory files (USER.md +
MEMORY.md) so the report agent can ground itself in the user's real
projects, plans and pain points rather than a stale hard-coded brief.

Other report responsibilities (day caching, 8:00 MSK regeneration rule,
session analysis, LLM generation) live client-side in the desktop plugin,
which already has ``session.list`` / ``llm.oneshot`` via host.request and
per-day localStorage caching.
"""

from pathlib import Path

from fastapi import APIRouter

from hermes_constants import get_hermes_home

router = APIRouter()


def _memory_files() -> list[tuple[str, str]]:
    """Return [(label, content)] for the profile's memory files, if present."""
    home = Path(get_hermes_home())
    mem_dir = home / "memories"
    parts: list[tuple[str, str]] = []
    for name in ("USER.md", "MEMORY.md"):
        f = mem_dir / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                text = ""
            if text:
                parts.append((name, text))
    # SOUL.md (identity/personality) enriches "from my point of view".
    soul = home / "SOUL.md"
    if soul.is_file():
        try:
            text = soul.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if text:
            parts.append(("SOUL.md", text))
    return parts


@router.get("/memory")
def get_memory() -> dict:
    """Return the profile's long-term memory for the report agent."""
    return {"memory": [{"name": n, "content": t} for n, t in _memory_files()], "error": None}