"""Live session registry — tracks all active AIAgent instances for inter-agent messaging.

Module-level registry that every AIAgent registers with on conversation start
and unregisters from on completion.  Enables the ``agent_message`` tool to
discover and deliver messages to other running sessions in the same process
(gateway multi-session, CLI with active subagents).

Thread-safe.  Records hold a weak-ish reference to the agent (not a WeakRef
because AIAgent is not weak-referenceable in all runtimes), plus metadata
for listing and delivery.

Key invariants:
  - Registration is per-conversation-turn, not per-agent-instance: gateway
    agents persist across turns but we register once and keep the record.
  - Subagent records are tagged ``role="child"``; top-level sessions are
    ``role="root"``.  Parent-child relationships are tracked via
    ``parent_session_id``.
  - The registry never blocks the agent loop: delivery to a busy agent
    queues a steer; delivery to an idle agent injects a new turn.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# session_id -> record dict
_sessions: Dict[str, Dict[str, Any]] = {}
# Counter for generating readable session labels
_label_counter = 0


def register_session(
    agent: Any,
    *,
    session_id: str,
    role: str = "root",
    parent_session_id: Optional[str] = None,
    name: Optional[str] = None,
    model: Optional[str] = None,
    platform: Optional[str] = None,
) -> str:
    """Register a live agent session.  Returns the session_id.

    Called from AIAgent at conversation start.  Safe to call multiple times
    with the same session_id — updates metadata only.
    """
    global _label_counter
    with _lock:
        existing = _sessions.get(session_id)
        if existing is not None:
            # Update metadata on re-registration (e.g. new turn)
            existing["agent"] = agent
            if model:
                existing["model"] = model
            if platform:
                existing["platform"] = platform
            existing["last_active"] = time.time()
            return session_id

        if name is None:
            _label_counter += 1
            name = f"session-{_label_counter}"

        _sessions[session_id] = {
            "session_id": session_id,
            "agent": agent,
            "role": role,
            "parent_session_id": parent_session_id,
            "name": name,
            "model": model or "",
            "platform": platform or "",
            "registered_at": time.time(),
            "last_active": time.time(),
            "busy": False,
            "_pending_messages": [],
        }
        logger.debug("live_session_registry: registered %s (%s)", session_id, name)
        return session_id


def unregister_session(session_id: str) -> None:
    """Remove a session from the registry."""
    with _lock:
        rec = _sessions.pop(session_id, None)
        if rec:
            logger.debug("live_session_registry: unregistered %s", session_id)


def mark_busy(session_id: str, busy: bool) -> None:
    """Update the busy flag for a session."""
    with _lock:
        rec = _sessions.get(session_id)
        if rec:
            rec["busy"] = busy
            rec["last_active"] = time.time()


def list_live_sessions(
    *,
    include_self: Optional[str] = None,
    role_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Snapshot of live sessions.  Excludes the ``agent`` callable.

    Args:
        include_self: if set, exclude this session_id from results.
        role_filter: if set, only return sessions with this role.
    """
    with _lock:
        items = []
        for sid, rec in _sessions.items():
            if include_self and sid == include_self:
                continue
            if role_filter and rec.get("role") != role_filter:
                continue
            items.append({
                k: v for k, v in rec.items()
                if k not in {"agent", "_pending_messages"}
            })
        return items


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get a session record by ID (includes the agent ref)."""
    with _lock:
        return _sessions.get(session_id)


def deliver_message(
    target_session_id: str,
    message: str,
    *,
    sender_session_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    """Deliver a message to a live session.

    Delivery modes (mirrors Prime Agent's agent_message):
      - ``auto``: steer if busy, deliver immediately if idle.
      - ``steer``: inject into active work (queue for next iteration boundary).
      - ``follow_up``: queue for after the current turn finishes.

    Returns a receipt dict:
      ``{"deliveryStatus": "delivered"|"queued"|"failed", "reason": str}``
    """
    with _lock:
        rec = _sessions.get(target_session_id)
        if rec is None:
            return {"deliveryStatus": "failed", "reason": "target session not found"}
        agent = rec.get("agent")
        if agent is None:
            return {"deliveryStatus": "failed", "reason": "target agent not available"}

        is_busy = rec.get("busy", False)
        full_msg = message
        if sender_name:
            full_msg = f"[Message from {sender_name}]: {message}"

        # Try steer for busy agents (auto/steer modes)
        if mode in ("auto", "steer") and is_busy:
            steer_fn = getattr(agent, "steer", None)
            if callable(steer_fn):
                try:
                    ok = bool(steer_fn(full_msg))
                    if ok:
                        return {"deliveryStatus": "delivered", "reason": "steered into active work"}
                except Exception as exc:
                    logger.debug("deliver_message steer failed: %s", exc)
                    # Fall through to queue
            # Steer failed or not available — queue if follow_up, else fail
            if mode == "steer":
                return {"deliveryStatus": "failed", "reason": "steer failed (agent may not accept steering)"}
            # auto mode: queue for later
            rec["_pending_messages"].append(full_msg)
            return {"deliveryStatus": "queued", "reason": "agent busy, queued for next turn"}

        if mode == "follow_up":
            rec["_pending_messages"].append(full_msg)
            return {"deliveryStatus": "queued", "reason": "queued for after current turn"}

        # Idle agent — inject as a new turn via _pending_input or equivalent
        inject_fn = getattr(agent, "inject_user_turn", None)
        if callable(inject_fn):
            try:
                inject_fn(full_msg)
                return {"deliveryStatus": "delivered", "reason": "injected as new turn"}
            except Exception as exc:
                logger.debug("deliver_message inject failed: %s", exc)
                return {"deliveryStatus": "failed", "reason": f"injection failed: {exc}"}

        # Fallback: queue
        rec["_pending_messages"].append(full_msg)
        return {"deliveryStatus": "queued", "reason": "no injection mechanism, queued"}


def drain_pending_messages(session_id: str) -> List[str]:
    """Drain queued messages for a session.  Called when a turn finishes."""
    with _lock:
        rec = _sessions.get(session_id)
        if not rec:
            return []
        msgs = rec["_pending_messages"]
        rec["_pending_messages"] = []
        return msgs


def find_by_name(name: str) -> Optional[str]:
    """Find a session_id by name (case-insensitive)."""
    name_lower = name.lower().strip()
    with _lock:
        for sid, rec in _sessions.items():
            if rec.get("name", "").lower() == name_lower:
                return sid
    return None
