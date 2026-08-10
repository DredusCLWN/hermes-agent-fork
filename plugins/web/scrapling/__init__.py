"""Scrapling web extract plugin — bundled, auto-loaded."""

from __future__ import annotations

from plugins.web.scrapling.provider import ScraplingWebExtractProvider


def register(ctx) -> None:
    """Register the Scrapling extract provider with the plugin context."""
    ctx.register_web_search_provider(ScraplingWebExtractProvider())
