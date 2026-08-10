"""Configurable tool-output truncation limits.

Ported from anomalyco/opencode PR #23770 (``feat(truncate): allow
configuring tool output truncation limits``).

OpenCode hardcoded ``MAX_LINES = 2000`` and ``MAX_BYTES = 50 * 1024``
as tool-output truncation thresholds. Hermes-agent had the same
hardcoded constants in two places:

* ``tools/terminal_tool.py`` — ``MAX_OUTPUT_CHARS = 50000`` (terminal
  stdout/stderr cap)
* ``tools/file_operations.py`` — ``MAX_LINES = 2000`` /
  ``MAX_LINE_LENGTH = 2000`` (read_file pagination cap + per-line cap)

This module centralises those values behind a single config section
(``tool_output`` in ``config.yaml``) so power users can tune them
without patching the source. The existing hardcoded numbers remain as
defaults, so behaviour is unchanged when the config key is absent.

Example ``config.yaml``::

    tool_output:
      max_bytes: 100000        # terminal output cap (chars)
      max_lines: 5000          # read_file pagination + truncation cap
      max_line_length: 2000    # per-line length cap before '... [truncated]'

The limits reader is defensive: any error (missing config file, invalid
value type, etc.) falls back to the built-in defaults so tools never
fail because of a malformed config.
"""

from __future__ import annotations

from typing import Any, Dict

# Hardcoded defaults — these match the pre-existing values, so adding
# this module is behaviour-preserving for users who don't set
# ``tool_output`` in config.yaml.
DEFAULT_MAX_BYTES = 50_000       # terminal_tool.MAX_OUTPUT_CHARS
DEFAULT_MAX_LINES = 2000         # file_operations.MAX_LINES
DEFAULT_MAX_LINE_LENGTH = 2000   # file_operations.MAX_LINE_LENGTH
DEFAULT_KEEP_FIRST_LINES = 50    # head lines kept on truncation
DEFAULT_KEEP_LAST_LINES = 80     # tail lines kept on truncation
DEFAULT_ARTIFACT_STORE_ENABLED = True
DEFAULT_ARTIFACT_TTL_DAYS = 7
DEFAULT_ARTIFACT_MAX_GB = 1
DEFAULT_MAX_MATCH_CHARS = 500  # per-match line cap for grep/search results

# Module-level cache — populated on first call.
# Avoids repeated config file I/O on every tool call.
_cached_limits: dict | None = None


def _coerce_positive_int(value: Any, default: int) -> int:
    """Return ``value`` as a positive int, or ``default`` on any issue."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return default
    if iv <= 0:
        return default
    return iv


def _coerce_bool(value: Any, default: bool) -> bool:
    """Return ``value`` as a bool, or ``default`` on any issue."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def get_tool_output_limits() -> Dict[str, int]:
    """Return resolved tool-output limits, reading ``tool_output`` from config.

    Keys: ``max_bytes``, ``max_lines``, ``max_line_length``. Missing or
    invalid entries fall through to the ``DEFAULT_*`` constants. This
    function NEVER raises.

    Result is cached for the process lifetime to avoid repeated disk I/O
    on every tool call. Call ``_reset_tool_output_limits_cache()`` in
    tests that need a fresh read after config changes.
    """
    global _cached_limits
    if _cached_limits is not None:
        return _cached_limits
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        section = cfg.get("tool_output") if isinstance(cfg, dict) else None
        if not isinstance(section, dict):
            section = {}
    except Exception:
        section = {}

    _cached_limits = {
        "max_bytes": _coerce_positive_int(section.get("max_bytes"), DEFAULT_MAX_BYTES),
        "max_lines": _coerce_positive_int(section.get("max_lines"), DEFAULT_MAX_LINES),
        "max_line_length": _coerce_positive_int(
            section.get("max_line_length"), DEFAULT_MAX_LINE_LENGTH
        ),
        "max_match_chars": _coerce_positive_int(
            section.get("max_match_chars"), DEFAULT_MAX_MATCH_CHARS
        ),
        "keep_first_lines": _coerce_positive_int(
            section.get("keep_first_lines"), DEFAULT_KEEP_FIRST_LINES
        ),
        "keep_last_lines": _coerce_positive_int(
            section.get("keep_last_lines"), DEFAULT_KEEP_LAST_LINES
        ),
        "artifact_store_enabled": _coerce_bool(
            section.get("artifact_store_enabled"), DEFAULT_ARTIFACT_STORE_ENABLED
        ),
        "artifact_ttl_days": _coerce_positive_int(
            section.get("artifact_ttl_days"), DEFAULT_ARTIFACT_TTL_DAYS
        ),
        "artifact_max_gb": _coerce_positive_int(
            section.get("artifact_max_gb"), DEFAULT_ARTIFACT_MAX_GB
        ),
    }
    return _cached_limits


def _reset_tool_output_limits_cache() -> None:
    """Reset the cached limits — for tests or after config hot-reload."""
    global _cached_limits
    _cached_limits = None


def get_max_bytes() -> int:
    """Shortcut for terminal-tool callers that only need the byte cap."""
    return get_tool_output_limits()["max_bytes"]


def get_max_lines() -> int:
    """Shortcut for file-ops callers that only need the line cap."""
    return get_tool_output_limits()["max_lines"]


def get_max_line_length() -> int:
    """Shortcut for file-ops callers that only need the per-line cap."""
    return get_tool_output_limits()["max_line_length"]


def get_keep_first_lines() -> int:
    """Shortcut for callers that need the head-line count on truncation."""
    return get_tool_output_limits()["keep_first_lines"]


def get_keep_last_lines() -> int:
    """Shortcut for callers that need the tail-line count on truncation."""
    return get_tool_output_limits()["keep_last_lines"]


def is_artifact_store_enabled() -> bool:
    """Shortcut for callers checking whether to save full output to artifacts."""
    return get_tool_output_limits()["artifact_store_enabled"]


def get_artifact_ttl_days() -> int:
    """Shortcut for artifact cleanup — max age in days."""
    return get_tool_output_limits()["artifact_ttl_days"]


def get_artifact_max_gb() -> int:
    """Shortcut for artifact cleanup — total size cap in GB."""
    return get_tool_output_limits()["artifact_max_gb"]


def get_max_match_chars() -> int:
    """Shortcut for search/grep callers — per-match line char cap."""
    return get_tool_output_limits()["max_match_chars"]
