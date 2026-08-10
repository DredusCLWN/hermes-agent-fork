"""Scrapling web extract — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`.
Extract-only — pair with ``ddgs`` or ``brave_free`` for search.

Capabilities:
- ``supports_search()``  -> False
- ``supports_extract()`` -> True

No API key needed. The ``scrapling`` package is an optional dependency;
``is_available()`` reflects whether the package is importable.

Extract strategy:
1. Try ``Fetcher`` (fast HTTP, TLS fingerprint impersonation) for static pages.
2. On failure (JS-heavy, Cloudflare, empty content) fall back to ``StealthyFetcher``
   (headless Chromium with anti-bot bypass).
3. Return clean text/markdown — Scrapling strips boilerplate automatically.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECS = 30
_STEALTHY_TIMEOUT_SECS = 45
_MAX_CONTENT_CHARS = 50000


class ScraplingWebExtractProvider(WebSearchProvider):
    """Scrapling extract provider — free, no API key, anti-bot bypass."""

    @property
    def name(self) -> str:
        return "scrapling"

    @property
    def display_name(self) -> str:
        return "Scrapling (free extract)"

    def is_available(self) -> bool:
        try:
            import scrapling  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from URLs using Scrapling.

        Tries fast HTTP fetch first, falls back to stealthy browser for
        JS-heavy or anti-bot-protected pages.
        """
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return [
                    {"url": u, "error": "Interrupted", "title": ""}
                    for u in urls
                ]
        except Exception:  # noqa: BLE001
            pass

        results: List[Dict[str, Any]] = []
        for url in urls:
            try:
                result = self._extract_single(url)
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scrapling extract error for %s: %s", url, exc)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": f"Scrapling extract failed: {exc}",
                        "metadata": {"sourceURL": url},
                    }
                )
        return results

    def _extract_single(self, url: str) -> Dict[str, Any]:
        """Extract content from a single URL.

        Strategy: Fetcher (HTTP) → StealthyFetcher (browser) fallback.
        """
        content = ""
        title = ""
        used_stealthy = False

        try:
            content, title = self._fetch_http(url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Scrapling HTTP fetch failed for %s: %s, trying stealthy", url, exc)
            try:
                content, title = self._fetch_stealthy(url)
                used_stealthy = True
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(
                    f"Both HTTP and stealthy fetch failed: {exc} / {exc2}"
                ) from exc2

        if not content or len(content.strip()) < 100:
            logger.debug("Scrapling HTTP content too short for %s, trying stealthy", url)
            if not used_stealthy:
                try:
                    content_stealthy, title_stealthy = self._fetch_stealthy(url)
                    if len(content_stealthy.strip()) > len(content.strip()):
                        content = content_stealthy
                        title = title_stealthy or title
                        used_stealthy = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Stealthy fallback also failed for %s: %s", url, exc)

        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n\n[... content truncated ...]"

        return {
            "url": url,
            "title": title,
            "content": content,
            "raw_content": content,
            "metadata": {
                "sourceURL": url,
                "title": title,
                "extractor": "stealthy" if used_stealthy else "http",
            },
        }

    @staticmethod
    def _fetch_http(url: str) -> tuple[str, str]:
        """Fast HTTP fetch via Scrapling Fetcher with TLS fingerprint."""
        from scrapling.fetch import Fetcher

        fetcher = Fetcher(timeout=_FETCH_TIMEOUT_SECS)
        page = fetcher.get(url)

        title = ""
        try:
            title_el = page.css_first("title")
            if title_el:
                title = title_el.text.strip()
        except Exception:  # noqa: BLE001
            pass

        try:
            h1 = page.css_first("h1")
            if h1 and not title:
                title = h1.text.strip()
        except Exception:  # noqa: BLE001
            pass

        content = page.get_all_text() if hasattr(page, "get_all_text") else str(page)
        return content, title

    @staticmethod
    def _fetch_stealthy(url: str) -> tuple[str, str]:
        """Stealthy browser fetch via Scrapling StealthyFetcher.

        Bypasses Cloudflare Turnstile/Interstitial, renders JS.
        """
        from scrapling.fetch import StealthyFetcher

        fetcher = StealthyFetcher(timeout=_STEALTHY_TIMEOUT_SECS)
        page = fetcher.fetch(url)

        title = ""
        try:
            title_el = page.css_first("title")
            if title_el:
                title = title_el.text.strip()
        except Exception:  # noqa: BLE001
            pass

        try:
            h1 = page.css_first("h1")
            if h1 and not title:
                title = h1.text.strip()
        except Exception:  # noqa: BLE001
            pass

        content = page.get_all_text() if hasattr(page, "get_all_text") else str(page)
        return content, title

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Scrapling (free extract)",
            "badge": "free · no key · extract only",
            "tag": (
                "Anti-bot content extraction via Scrapling — no API key. "
                "Bypasses Cloudflare, renders JS, returns clean text. "
                "Pair with ddgs or brave_free for search. "
                "Install: pip install scrapling"
            ),
            "env_vars": [],
            "post_setup": "scrapling",
        }
