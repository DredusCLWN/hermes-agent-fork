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
        """Optional eager teardown hook. Default no-op; setting the stop_event
        is the primary stop signal. Override for providers holding external
        resources (queue consumers, HTTP servers)."""
        return None

    # --- Optional hooks for external providers (added Phase 4). --------------
    # All default-safe so the built-in inherits working behavior without
    # overriding. Keep these NON-abstract — see test_abc_growth_stays_additive.

    def on_jobs_changed(self) -> None:
        """Called after a successful store mutation (create/update/remove/
        pause/resume). External providers reconcile their registry here (e.g.
        Chronos re-provisions/cancels the affected one-shot via NAS).
        Built-in: no-op (it re-reads jobs.json on every tick)."""
        return None

    def register_job(self, job: dict[str, Any]) -> None:
        """Register the first external trigger for one newly persisted job.

        The built-in provider reads the local store on every tick, so its
        default is a no-op. External providers override this when creating a
        job requires a remote registration before callers can honestly report
        that the job is scheduled.
        """
        return None

    def recover_interrupted(self) -> int:
        """Run profile-local attempt recovery for every provider lifecycle."""
        from cron.executions import recover_interrupted_executions

        return recover_interrupted_executions()

    def fire_due(self, job_id: str, *, adapters: Any = None, loop: Any = None) -> bool:
        """Run a single job NOW via the shared orchestrator. Called by the
        inbound fire webhook when an external scheduler signals a job is due.

        The default claims the job with a store-level compare-and-set
        (multi-machine at-most-once), then runs it via the shared
        ``run_one_job`` body. Built-in never calls this (it has its own tick
        loop); an external provider routes its inbound fire here.

        Returns True if THIS caller claimed and ran the job, False if the claim
        was lost (another machine/retry won it) or the job no longer exists.
        """
        from cron.jobs import claim_job_for_fire, get_job
        from cron.executions import create_execution
        from cron.scheduler import run_one_job

        if not claim_job_for_fire(job_id):
            return False  # another machine already claimed this fire
        job = get_job(job_id)
        if job is None:
            return False  # job removed (e.g. repeat-N exhausted) between arm and fire
        job["execution_id"] = create_execution(job_id, source=self.name)["id"]
        return run_one_job(job, adapters=adapters, loop=loop)

    def reconcile(self) -> None:
        """Converge the external registry toward jobs.json (the desired state):
        arm missing one-shots, cancel orphaned ones, re-arm changed times.
        Built-in: no-op."""
        return None


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
