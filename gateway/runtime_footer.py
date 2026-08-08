"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled.  Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                       # off by default
        fields: [model, context_pct, cwd]   # order shown; drop any to hide

Available fields:
    model         — bare model id, vendor prefix dropped (``gpt-5.4``)
    context_pct   — last-call context occupancy as a percent (``5%``)
    latency       — wall-clock duration of the turn (``22s``, ``1m05s``)
    cwd           — home-relative working dir (``~``)
    token_savings — aggregated tokens saved by all optimization tools
                   (``⚡ 12.4k saved: cache 8k · caveman 3.2k · ponytail 2k · compress 1.2k``)

``latency`` is opt-in: it is NOT in the default field set, so a footer whose
``fields`` are unset renders exactly as before.

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials.  When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "

def _fmt_tokens(n: int) -> str:
    """Compact token count: 1.2k, 15.7k, 3M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS)}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]

    # Auto-append token_savings when any optimization is active and user
    # hasn't explicitly listed it. Shows aggregated savings without extra config.
    # Always auto-append: prompt caching and compression are always on,
    # so there are almost always savings to show.
    if "token_savings" not in resolved["fields"]:
        resolved["fields"].append("token_savings")

    return resolved


def _format_latency(seconds: float) -> str:
    """Humanize a turn duration: ``<1s``, ``22s``, ``1m05s``."""
    if seconds < 1:
        return "<1s"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    m, sec = divmod(total, 60)
    return f"{m}m{sec:02d}s"


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    turn_seconds: Optional[float] = None,
    output_tokens: int = 0,
    caveman_active: bool = False,
    caveman_mode: str = "auto",
    cache_read_tokens: int = 0,
    compression_saved_tokens: int = 0,
    ponytail_active: bool = False,
    fields: Iterable[str] = _DEFAULT_FIELDS,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    for field in fields:
        if field == "model":
            m = _model_short(model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"{pct}%")
        elif field == "latency":
            # Wall-clock turn duration. Skipped when the caller supplied no
            # timing (call sites that don't measure) or the value is negative.
            if turn_seconds is not None and turn_seconds >= 0:
                parts.append(_format_latency(turn_seconds))
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        elif field == "token_savings":
            # Aggregate savings from all optimization tools.
            savings_parts: list[str] = []
            total_saved = 0

            # 1. Prompt caching — real data from API (cache_read_tokens).
            #    These are input tokens that were cached and not re-billed
            #    at full price. Count them as saved.
            if cache_read_tokens > 0:
                total_saved += cache_read_tokens
                savings_parts.append(f"cache {_fmt_tokens(cache_read_tokens)}")

            # 2. Caveman + Ponytail — estimated output-token reductions.
            #    Shared helper: both rates compress the same full baseline,
            #    so they combine multiplicatively (savings compound).
            from hermes_cli.style_savings import estimate_style_savings
            _style = estimate_style_savings(
                output_tokens,
                caveman_active=caveman_active,
                ponytail_active=ponytail_active,
                caveman_mode=caveman_mode,
            )
            if _style["caveman_saved"] > 0:
                total_saved += _style["caveman_saved"]
                savings_parts.append(
                    f"caveman{_style['mode_tag']} {_fmt_tokens(_style['caveman_saved'])}"
                )
            if _style["ponytail_saved"] > 0:
                total_saved += _style["ponytail_saved"]
                savings_parts.append(f"ponytail {_fmt_tokens(_style['ponytail_saved'])}")

            # 3. Context compression — estimated tokens saved by compression.
            if compression_saved_tokens > 0:
                total_saved += compression_saved_tokens
                savings_parts.append(f"compress {_fmt_tokens(compression_saved_tokens)}")

            if total_saved > 0:
                parts.append(f"\u26a1 {_fmt_tokens(total_saved)} saved: " + " · ".join(savings_parts))
        # Unknown field names are silently ignored.

    if not parts:
        return ""
    return _SEP.join(parts)


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    turn_seconds: Optional[float] = None,
    output_tokens: int = 0,
    caveman_active: bool = False,
    caveman_mode: str = "auto",
    cache_read_tokens: int = 0,
    compression_saved_tokens: int = 0,
    ponytail_active: bool = False,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.

    ``turn_seconds`` is the wall-clock duration of the agent run, measured by
    the caller with ``time.monotonic()``.  Callers that don't measure it leave
    it ``None`` and the ``latency`` field is skipped.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    return format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        turn_seconds=turn_seconds,
        output_tokens=output_tokens,
        caveman_active=caveman_active,
        caveman_mode=caveman_mode,
        cache_read_tokens=cache_read_tokens,
        compression_saved_tokens=compression_saved_tokens,
        ponytail_active=ponytail_active,
        fields=cfg.get("fields") or _DEFAULT_FIELDS,
    )
