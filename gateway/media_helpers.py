"""Media helper functions extracted from gateway/run.py.

Pure functions for media type detection, placeholder generation,
and auto-append media tag collection from tool results.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from gateway.platforms.base import BasePlatformAdapter, MessageType

_AUTO_APPEND_MEDIA_TOOL_NAMES = {
    "text_to_speech",
    "text_to_speech_tool",
    "image_generate",
    "bfl_flux3_get_result",
}

_JSON_MEDIA_TOOL_PATH_FIELDS = ("host_image", "image", "agent_visible_image")

_TOOL_MEDIA_RE = re.compile(
    r'MEDIA:((?:[A-Za-z]:[/\\]|/|~\/)\S+\.(?:png|jpe?g|gif|webp|'
    r'mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|'
    r'flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|'
    r'txt|csv|apk|ipa))',
    re.IGNORECASE,
)


def _event_media_type_at(event, index: int) -> str:
    """Return the per-attachment MIME for the attachment at *index*.

    Empty string when the platform didn't populate a per-file MIME for
    that slot (some adapters only set a message-level type).
    """
    media_types = getattr(event, "media_types", None) or []
    return media_types[index] if index < len(media_types) else ""


def _event_media_is_image(event, index: int) -> bool:
    """True if the attachment at *index* is an image.

    Trust the per-attachment MIME when present. Only fall back to the
    message-level ``PHOTO`` type when this attachment's MIME is unknown --
    otherwise a document (or any non-image) uploaded alongside an image in
    the same message gets mis-routed as an image, base64'd into a vision
    content part, and the provider 400s ("Could not process image").
    """
    mtype = _event_media_type_at(event, index)
    if mtype:
        return mtype.startswith("image/")
    return getattr(event, "message_type", None) == MessageType.PHOTO


def _event_media_is_audio(event, index: int) -> bool:
    """True if the attachment at *index* is audio (per-attachment MIME first)."""
    mtype = _event_media_type_at(event, index)
    if mtype:
        return mtype.startswith("audio/")
    return getattr(event, "message_type", None) in {MessageType.AUDIO, MessageType.AUDIO}


def _event_media_is_video(event, index: int) -> bool:
    """True if the attachment at *index* is video (per-attachment MIME first)."""
    mtype = _event_media_type_at(event, index)
    if mtype:
        return mtype.startswith("video/")
    return getattr(event, "message_type", None) == MessageType.VIDEO


def _build_media_placeholder(event) -> str:
    """Build a text placeholder for media-only events so they aren't dropped.

    When a photo/document is queued during active processing and later
    dequeued, only .text is extracted.  If the event has no caption,
    the media would be silently lost.  This builds a placeholder that
    the vision enrichment pipeline will replace with a real description.
    """
    parts = []
    media_urls = getattr(event, "media_urls", None) or []
    for i, url in enumerate(media_urls):
        if _event_media_is_image(event, i):
            parts.append(f"[User sent an image: {url}]")
        elif _event_media_is_audio(event, i):
            parts.append(f"[User sent audio: {url}]")
        elif _event_media_is_video(event, i):
            parts.append(f"[User sent a video: {url}]")
        else:
            parts.append(f"[User sent a file: {url}]")
    return "\n".join(parts)


def _build_document_context_note(display_name: str, agent_path: str, mtype: str) -> str:
    """Context note prepended to a user turn when they attach a document.

    Text documents (``text/*``) have their content inlined upstream by the
    platform adapter, so the note just confirms that and records the path.

    Binary documents (PDF, DOCX, XLSX, …) cannot be inlined as text. The note
    must tell the agent to *extract* the text itself before answering — earlier
    wording ("Ask the user what they'd like you to do with it") steered the
    model into punting back to the user, which is why attached PDFs/DOCX looked
    "unreadable" to the agent even though it has the tools to read them.
    """
    if mtype.startswith("text/"):
        return (
            f"[The user sent a text document: '{display_name}'. "
            f"Its content has been included below. "
            f"The file is also saved at: {agent_path}]"
        )
    return (
        f"[The user sent a document: '{display_name}'. It is saved at: {agent_path}. "
        f"Its text is not inlined here (it's a binary format such as PDF or DOCX). "
        f"To read it, extract the document's text yourself — for example with the "
        f"terminal tool or the ocr-and-documents skill — before answering, instead "
        f"of asking the user to paste the contents.]"
    )


def _collect_auto_append_media_tags(
    messages: List[Dict[str, Any]],
    history_offset: int = 0,
    history_media_paths: Optional[set] = None,
) -> List[str]:
    """Collect real media tags from current-turn producer-tool results only.

    Two layered guards keep stale/example MEDIA: strings out of the reply:

    1. Producer-tool allowlist: only tools that intentionally emit deliverable
       artifacts (TTS) are eligible. Documentation, logs, and search results can
       contain example strings such as MEDIA:/absolute/path/to/file, which must
       never be delivered as attachments. (Fixes the original report behind #16721.)
    2. Current-turn isolation: only messages produced this turn are scanned, so a
       tool result from an earlier turn (still present in the full message list)
       cannot leak onto a later text-only reply (#34608).

    Mid-run context compression can rewrite/shrink the message list below the
    original history length. When that happens the slice boundary is no longer
    trustworthy, so fall back to scanning every message and rely on
    ``history_media_paths`` for dedup, preserving the compression-safe behaviour
    of #160. The producer-tool allowlist still applies on the fallback path.
    """
    history_media_paths = history_media_paths or set()
    # Only trust the slice boundary when the message list still contains the
    # full history prefix. Otherwise scan everything (compression-safe fallback).
    if history_offset and len(messages) >= history_offset:
        new_messages = messages[history_offset:]
    else:
        new_messages = messages

    tool_name_by_call_id: Dict[str, str] = {}
    for msg in new_messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            call_id = call.get("id") or call.get("call_id")
            fn = call.get("function") or {}
            name = str(fn.get("name") or call.get("name") or "")
            if call_id and name:
                tool_name_by_call_id[str(call_id)] = name

    media_tags: List[str] = []
    for msg in new_messages:
        if msg.get("role") not in ("tool", "function"):
            continue
        call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
        if tool_name_by_call_id.get(call_id) not in _AUTO_APPEND_MEDIA_TOOL_NAMES:
            continue
        content = str(msg.get("content") or "")
        tool_name = tool_name_by_call_id.get(call_id)
        # JSON-payload tools (image_generate) return a local-file path in a
        # known field rather than a MEDIA: tag. Extract it so delivery is
        # deterministic even when the model omits the path from its reply.
        if tool_name == "image_generate" and "MEDIA:" not in content:
            try:
                payload = json.loads(content)
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success"):
                for field in _JSON_MEDIA_TOOL_PATH_FIELDS:
                    path = payload.get(field)
                    if (isinstance(path, str)
                            and _TOOL_MEDIA_RE.fullmatch(f"MEDIA:{path}")
                            and path not in history_media_paths):
                        media_tags.append(f"MEDIA:{path}")
                        break
            continue
        if "MEDIA:" not in content:
            continue
        for match in _TOOL_MEDIA_RE.finditer(content):
            path = match.group(1).strip().rstrip('",}')
            if path and path not in history_media_paths:
                media_tags.append(f"MEDIA:{path}")
    return media_tags


def _collect_history_media_paths(agent_history: List[Dict[str, Any]]) -> set:
    """Collect every media path already delivered in prior assistant/tool output.

    Used to dedup auto-appended and model-emitted MEDIA tags so the same file
    is not re-sent on later turns. Covers three delivery shapes:
      * ``MEDIA:<path>`` text tags in tool results,
      * ``MEDIA:<path>`` text tags in assistant messages (model-generated tags),
      * ``image_generate`` JSON-payload paths (``host_image`` / ``image`` /
        ``agent_visible_image``), which carry no MEDIA: tag.

    Missing the JSON-payload shape caused #46627; missing the assistant-message
    shape caused repeated delivery when the model echoed a previous MEDIA tag.
    """
    paths: set = set()
    tool_name_by_call_id: Dict[str, str] = {}

    def _add_text_media_paths(content: str) -> None:
        for match in _TOOL_MEDIA_RE.finditer(content):
            path = match.group(1).strip().rstrip('",}')
            if path:
                paths.add(path)
        # The regex alone misses quoted and spaced paths that the delivery
        # pipeline's extract_media grammar accepts — collect through the same
        # extractor so the dedup set sees every path that could actually have
        # been delivered.
        media_files, _ = BasePlatformAdapter.extract_media(content)
        paths.update(path for path, _is_voice in media_files)

    for msg in agent_history:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                cid = call.get("id") or call.get("call_id")
                fn = call.get("function") or {}
                name = str(fn.get("name") or call.get("name") or "")
                if cid and name:
                    tool_name_by_call_id[str(cid)] = name
    for msg in agent_history:
        role = msg.get("role")
        if role == "assistant":
            content = str(msg.get("content", "") or "")
            if "MEDIA:" in content:
                _add_text_media_paths(content)
            continue
        if role not in {"tool", "function"}:
            continue
        content = str(msg.get("content", "") or "")
        if "MEDIA:" in content:
            _add_text_media_paths(content)
            continue
        cid = str(msg.get("tool_call_id") or msg.get("call_id") or "")
        if tool_name_by_call_id.get(cid) == "image_generate":
            try:
                payload = json.loads(content)
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success"):
                for field in _JSON_MEDIA_TOOL_PATH_FIELDS:
                    jp = payload.get(field)
                    if isinstance(jp, str) and jp:
                        paths.add(jp)
                        break
    return paths
