"""Token budget manager — automatic context-usage monitoring.

Checks context fill before each API call and emits a warning at
``warning_threshold`` (default 80%), then forces emergency compression at
``hard_limit`` (default 95%).

Does NOT mutate system prompt or messages — cache-safe by design. The budget
manager is a read-only monitor that signals the conversation loop to act.

Usage::

    from agent.budget_manager import check_budget, BudgetStatus

    status = check_budget(context_tokens=45000, model_context_length=200000)
    if status == BudgetStatus.WARNING:
        # notify user
    elif status == BudgetStatus.CRITICAL:
        # trigger emergency compression
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict

logger = logging.getLogger(__name__)


class BudgetStatus(Enum):
    """Context budget status returned by ``check_budget``."""
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


_cached_budget_config: dict | None = None


def _get_budget_config() -> Dict[str, Any]:
    """Return resolved token_budget config, reading from config.yaml.

    Cached for process lifetime. Never raises.
    """
    global _cached_budget_config
    if _cached_budget_config is not None:
        return _cached_budget_config

    defaults = {
        "warning_threshold": 0.80,
        "hard_limit": 0.95,
        "per_turn_output_cap": 0,
    }

    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        agent = cfg.get("agent") if isinstance(cfg, dict) else None
        if not isinstance(agent, dict):
            agent = {}
        section = agent.get("token_budget")
        if not isinstance(section, dict):
            section = {}
    except Exception:
        section = {}

    _cached_budget_config = {
        "warning_threshold": _coerce_float(
            section.get("warning_threshold"), defaults["warning_threshold"]
        ),
        "hard_limit": _coerce_float(
            section.get("hard_limit"), defaults["hard_limit"]
        ),
        "per_turn_output_cap": _coerce_int(
            section.get("per_turn_output_cap"), defaults["per_turn_output_cap"]
        ),
    }
    return _cached_budget_config


def _coerce_float(value: Any, default: float) -> float:
    try:
        v = float(value)
        return v if 0.0 < v <= 1.0 else default
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        v = int(value)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _reset_budget_config_cache() -> None:
    """Reset cached config — for tests or after config hot-reload."""
    global _cached_budget_config
    _cached_budget_config = None


def check_budget(context_tokens: int, model_context_length: int) -> BudgetStatus:
    """Check context usage against configured thresholds.

    Returns ``BudgetStatus.OK`` when usage is below warning_threshold,
    ``BudgetStatus.WARNING`` when at or above warning_threshold but below
    hard_limit, and ``BudgetStatus.CRITICAL`` when at or above hard_limit.

    If ``model_context_length`` is 0 or negative, returns ``OK`` (can't
    determine usage).
    """
    if model_context_length <= 0 or context_tokens <= 0:
        return BudgetStatus.OK

    config = _get_budget_config()
    ratio = context_tokens / model_context_length

    if ratio >= config["hard_limit"]:
        return BudgetStatus.CRITICAL
    if ratio >= config["warning_threshold"]:
        return BudgetStatus.WARNING
    return BudgetStatus.OK


def get_warning_threshold() -> float:
    """Shortcut for callers that need the warning threshold."""
    return _get_budget_config()["warning_threshold"]


def get_hard_limit() -> float:
    """Shortcut for callers that need the hard limit."""
    return _get_budget_config()["hard_limit"]


def get_per_turn_output_cap() -> int:
    """Shortcut for callers that need the per-turn output token cap.

    0 = no cap.
    """
    return _get_budget_config()["per_turn_output_cap"]
