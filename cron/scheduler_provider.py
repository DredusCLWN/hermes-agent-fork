"""Cron scheduler provider ABC + built-in in-process ticker.

Defines the ``CronScheduler`` interface that external providers (e.g.
``plugins/cron_providers/chronos/``) implement, plus the default
``InProcessCronScheduler`` that runs the 60-second tick loop from
``cron/scheduler.py``.

``resolve_cron_scheduler()`` reads ``cron.provider`` from config and
returns the matching provider instance, falling back to the built-in
ticker when the provider is unknown or unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CronScheduler:
    """Abstract base for cron scheduler providers.

    A provider decides *when* due jobs fire. The built-in
    ``InProcessCronScheduler`` ticks every 60 seconds; external providers
    (e.g. Chronos) arm one-shots via a remote scheduler and return
    immediately.
    """

    @property
    def name(self) -> str:
        raise NotImplementedError

    def is_available(self) -> bool:
        """Offline check — no network. True if the provider is configured."""
        return True

    def start(self, stop_event: threading.Event, *, adapters: Any = None, loop: Any = None, interval: int = 60) -> None:
        """Start the scheduler. May block (in-process ticker) or return
        immediately (external arm-and-return providers)."""
        raise NotImplementedError

    def stop(self) -> None:
        """Release resources. Called on shutdown."""
        pass

    def on_jobs_changed(self) -> None:
        """Notify the provider that jobs were created/updated/removed.
        External providers reconcile their armed one-shots here."""
        pass

    def fire_due(self, job_id: str, *, adapters: Any = None, loop: Any = None) -> bool:
        """Run a single due job. Returns True if the job ran."""
        from cron.jobs import get_job
        from cron.scheduler import run_one_job
        job = get_job(job_id)
        if not job:
            return False
        return run_one_job(job, adapters=adapters, loop=loop)


class InProcessCronScheduler(CronScheduler):
    """Built-in 60-second tick loop using ``cron.scheduler.tick``."""

    @property
    def name(self) -> str:
        return "in-process"

    def start(self, stop_event: threading.Event, *, adapters: Any = None, loop: Any = None, interval: int = 60) -> None:
        from cron.scheduler import tick

        while not stop_event.wait(interval):
            try:
                tick(verbose=False, adapters=adapters, loop=loop)
            except Exception as e:
                logger.debug("In-process cron tick failed: %s", e)

        # Write the success marker on a clean exit so staleness detection
        # doesn't flag a graceful shutdown as a dead ticker.
        try:
            from cron.jobs import TICKER_SUCCESS_FILE
            import time as _time
            TICKER_SUCCESS_FILE.write_text(str(_time.time()), encoding="utf-8")
        except Exception:
            pass


def resolve_cron_scheduler() -> CronScheduler:
    """Return the active cron scheduler provider.

    Reads ``cron.provider`` from config. Empty or unknown → built-in
    in-process ticker. A named provider is loaded from
    ``plugins/cron_providers/`` and must report ``is_available()``.
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        provider_name = str((cfg.get("cron") or {}).get("provider", "") or "").strip()
    except Exception:
        provider_name = ""

    if not provider_name:
        return InProcessCronScheduler()

    try:
        from plugins.cron_providers import load_cron_scheduler
        provider = load_cron_scheduler(provider_name)
        if provider and provider.is_available():
            return provider
        logger.info("Cron provider '%s' not available, falling back to in-process ticker", provider_name)
    except Exception as e:
        logger.debug("Failed to load cron provider '%s': %s", provider_name, e)

    return InProcessCronScheduler()
