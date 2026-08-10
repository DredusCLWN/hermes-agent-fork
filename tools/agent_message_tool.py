#!/usr/bin/env python3
"""Agent Message Tool — direct inter-agent messaging between live sessions.

Allows the model to send messages to other running agent sessions (siblings,
children, or parents) and list active sessions.  Mirrors Prime Agent's
``agent_message`` capability but adapted to Hermes's tool-registry architecture.

Two tools are registered:

  - ``agent_message_send`` — deliver a message to a target session by name or ID.
    Delivery modes: ``auto`` (steer if busy, inject if idle), ``steer`` (inject
    into active work), ``follow_up`` (queue for after current turn).

  - ``agent_message_list`` — list live sessions available for messaging.

Both tools are in the ``delegation`` toolset alongside ``delegate_task``.
"""

import json
import logging
from typing import Any, Dict

from tools.registry import registry

logger = logging.getLogger(__name__)


_AGENT_MESSAGE_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": (
                "Name or session ID of the target agent. Use 'parent' to "
                "message the parent session, or a name from agent_message_list."
            ),
        },
        "message": {
            "type": "string",
            "description": "The message text to deliver to the target agent.",
        },
        "mode": {
            "type": "string",
            "enum": ["auto", "steer", "follow_up"],
            "description": (
                "Delivery mode: 'auto' steers a busy target and delivers to "
                "an idle one; 'steer' intentionally injects into active work; "
                "'follow_up' waits until the target's current work finishes."
            ),
            "default": "auto",
        },
    },
    "required": ["target", "message"],
}

_AGENT_MESSAGE_LIST_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _agent_message_send_handler(args: Dict[str, Any], **kw) -> str:
    """Deliver a message to a live agent session."""
    from agent.live_session_registry import (
        deliver_message,
        find_by_name,
        get_session,
        list_live_sessions,
    )

    target = args.get("target", "").strip()
    message = args.get("message", "").strip()
    mode = args.get("mode", "auto")

    if not target:
        return json.dumps({"error": "target is required"})
    if not message:
        return json.dumps({"error": "message is required"})

    # Resolve 'parent' alias
    sender_session_id = _get_current_session_id(kw)
    if target.lower() == "parent":
        rec = get_session(sender_session_id) if sender_session_id else None
        if rec and rec.get("parent_session_id"):
            target = rec["parent_session_id"]
        else:
            return json.dumps({"error": "no parent session found for this agent"})

    # Resolve target by name, then by session_id
    target_sid = find_by_name(target)
    if target_sid is None:
        # Maybe it's already a session_id
        rec = get_session(target)
        if rec is not None:
            target_sid = target
    if target_sid is None:
        live = list_live_sessions()
        names = [r.get("name", r.get("session_id", "?")) for r in live]
        return json.dumps({
            "error": f"target '{target}' not found",
            "available": names,
        })

    # Get sender name
    sender_rec = get_session(sender_session_id) if sender_session_id else None
    sender_name = sender_rec.get("name") if sender_rec else None

    receipt = deliver_message(
        target_sid,
        message,
        sender_session_id=sender_session_id,
        sender_name=sender_name,
        mode=mode,
    )
    return json.dumps(receipt, ensure_ascii=False)


def _agent_message_list_handler(args: Dict[str, Any], **kw) -> str:
    """List live agent sessions available for messaging."""
    from agent.live_session_registry import list_live_sessions

    sender_session_id = _get_current_session_id(kw)
    sessions = list_live_sessions(include_self=sender_session_id)
    if not sessions:
        return json.dumps({"sessions": [], "note": "no other live sessions available"})

    # Compact view
    view = []
    for s in sessions:
        view.append({
            "name": s.get("name", ""),
            "session_id": s.get("session_id", ""),
            "role": s.get("role", ""),
            "model": s.get("model", ""),
            "busy": s.get("busy", False),
            "parent": s.get("parent_session_id") or None,
        })
    return json.dumps({"sessions": view}, ensure_ascii=False)


def _get_current_session_id(kw: Dict[str, Any]) -> str | None:
    """Extract the current agent's session_id from tool kwargs."""
    agent = kw.get("agent")
    if agent is not None:
        return getattr(agent, "session_id", None)
    return None


registry.register(
    name="agent_message_send",
    toolset="delegation",
    schema=_AGENT_MESSAGE_SEND_SCHEMA,
    handler=_agent_message_send_handler,
    emoji="📨",
)

registry.register(
    name="agent_message_list",
    toolset="delegation",
    schema=_AGENT_MESSAGE_LIST_SCHEMA,
    handler=_agent_message_list_handler,
    emoji="📋",
)
