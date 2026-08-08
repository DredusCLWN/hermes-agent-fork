"""Pure helpers for the desktop "Transfer to new session" action.

A transfer creates a continuation session (same project/model/cwd) that
carries context WITHOUT the user reading it as a cold start: a short
aux-model summary of the whole conversation, followed by the most recent
tail verbatim. The module is deliberately free of gateway globals so the
head/tail + seed logic is unit-testable as pure functions.

``summarize_head_async`` is the ONLY function that touches an LLM; it is best
effort and returns "" on any failure so a transfer NEVER depends on the aux
lane being healthy.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many of the most recent user turns to carry verbatim (in addition to
# the summary). The summary covers everything older.
DEFAULT_TAIL_USER_TURNS = 10

# Bounded: a transfer is an explicit user action; wait up to this for a
# summary before falling back to tail-only.
SUMMARY_TIMEOUT_SECONDS = 45


def split_user_turns(turns: List[Dict[str, str]], tail_n: int) -> int:
    """Return the index of the first turn included in the verbatim tail.

    ``turns`` is a list of ``{"role", "text"}`` with text already rendered
    for display (no tool dumps). Only ``role == "user"`` counts toward the
    tail window. Returns the start index such that ``turns[start:]`` is the
    tail (0 if there are fewer than ``tail_n`` user turns).
    """
    if not turns:
        return 0
    user_indices = [i for i, t in enumerate(turns) if t.get("role") == "user"]
    if not user_indices or tail_n <= 0:
        return 0
    first_kept = user_indices[max(0, len(user_indices) - tail_n)]
    # Nothing between the last kept user turn and the index 0 of the session.
    return max(0, first_kept)


def build_summary_prompt(head_turns: List[Dict[str, Any]]) -> str:
    """One-shot prompt compressing the older part of a session.

    Preserves what matters (active goal, decisions, unresolved threads, facts)
    in the conversation's own language; drops tool dumps and mechanical
    history.
    """
    lines = []
    for t in head_turns:
        text = (t.get("text") or "").strip()
        if not text:
            continue
        role = "USER" if t.get("role") == "user" else ("ASSISTANT" if t.get("role") == "assistant" else "SYS")
        text = text[:6000]  # cap a giant middle blob from blowing aux context
        lines.append(f"{role}: {text}")
    body = "\n\n".join(lines) if lines else "(no prior conversation)"
    return textwrap.dedent(
        f"""\
        You are preparing a seamless continuation of an ongoing conversation.
        Produce a SHORT factual summary (max ~700 words) of everything that
        happened BEFORE the point marked below, in the same language the
        conversation is in.

        Include:
        - The user's active goal / current task and progress so far.
        - Decisions, constraints and preferences that still matter.
        - New or important facts the continuation must not re-discover.
        - Any open questions or next steps.

        Do NOT include: tool output dumps, stack traces, boilerplate. Write as
        context a colleague would hand over, not a log.

        ---- conversation to summarize ----
        {body}
        """
    )


def assemble_seed(
    summary: Optional[str],
    tail_turns: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build the seed ``{role, content}`` list for the new session.

    A non-empty summary becomes a leading assistant message (so the new
    session's model sees the carried context without a user turn reading as
    "new"); the verbatim tail follows. Empty summary -> tail only; the
    continuation still inherits model/cwd/project, so it never reads as a cold
    start.
    """
    seed: List[Dict[str, str]] = []
    if summary and summary.strip():
        seed.append({"role": "assistant", "content": summary.strip()})
    for t in tail_turns:
        text = (t.get("text") or "").strip()
        if not text:
            continue
        role = t.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        seed.append({"role": role, "content": text})
    return seed


async def summarize_head_async(
    head_turns: List[Dict[str, Any]],
    *,
    timeout: float = SUMMARY_TIMEOUT_SECONDS,
) -> str:
    """Best-effort aux summary of the head. Returns "" on any failure."""
    if not head_turns:
        return ""
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client

        client, model_slug = get_async_text_auxiliary_client("compression")
        if client is None or not model_slug:
            logger.warning("transfer: no aux provider for summary; tail-only fallback")
            return ""
        prompt = build_summary_prompt(head_turns)
        text = await asyncio.wait_for(
            _run_completion(client, model_slug, prompt),
            timeout=timeout,
        )
        return (text or "").strip()
    except asyncio.TimeoutError:
        logger.warning("transfer: summary timed out after %ss; tail-only fallback", timeout)
        return ""
    except Exception as exc:  # noqa: BLE001 — never let a transfer depend on the LLM
        logger.warning("transfer: summary failed (%s); tail-only fallback", exc)
        return ""


async def _run_completion(client: Any, model: str, prompt: str) -> str:
    """One aux chat-completion; tolerant of per-client kwarg differences."""
    messages = [
        {"role": "system", "content": "You produce concise handoff summaries for a Hermes agent."},
        {"role": "user", "content": prompt},
    ]
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=900,
        )
    except TypeError:
        # Some clients/adapters reject `temperature`/`max_tokens` (e.g. Codex
        # Responses shim) — retry minimal.
        resp = await client.chat.completions.create(model=model, messages=messages)
    except Exception:
        raise
    if not getattr(resp, "choices", None):
        return ""
    content = getattr(resp.choices[0].message, "content", None)
    return content if isinstance(content, str) else ""