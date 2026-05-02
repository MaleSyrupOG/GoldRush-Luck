"""``PeriodicWorker`` — base class for the Epic 8 background workers.

Encapsulates the asyncio-loop scaffolding shared by every Epic 8
worker:

- ``start()`` is idempotent — calling twice does not spawn two loops.
- ``stop()`` is awaitable — the caller blocks until the loop has
  exited cleanly (so the ``DwBot.close_pool`` shutdown flow is
  deterministic).
- ``tick()`` is wrapped in a broad ``except`` so a transient error
  (DB blip, Discord API hiccup) doesn't kill the loop. The next
  iteration retries.
- The first ``tick`` runs immediately on ``start()`` so admins see
  effects without waiting ``interval_seconds`` first.

Subclasses override ``async def tick(self) -> None``. They keep their
own state (pool, bot, etc.) as instance attributes — the base class
is concerned ONLY with scheduling.

The pre-existing ``OnlineCashiersUpdater`` in
``goldrush_deposit_withdraw.cashiers.live_updater`` predates this
class and is left as-is to avoid touching a working component.
Future cleanup story can migrate it once Epic 8 stabilises.
"""

from __future__ import annotations

import asyncio

import structlog

_log = structlog.get_logger(__name__)


class PeriodicWorker:
    """Base class for a cancellable, broadly-exception-safe loop.

    Args:
        name: A short slug used in log lines (``worker=ticket_timeout``).
        interval_seconds: Sleep between iterations. The first tick is
            immediate; subsequent ticks wake up every ``interval_seconds``
            (subject to ``stop()``).
    """

    def __init__(self, *, name: str, interval_seconds: float) -> None:
        self.name = name
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def tick(self) -> None:  # pragma: no cover — abstract
        raise NotImplementedError(
            "subclasses must override `async def tick(self) -> None`"
        )

    def start(self) -> None:
        """Spawn the loop. Idempotent — calling twice is a no-op."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=f"{self.name}-worker")

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to exit cleanly."""
        if self._task is None:
            return
        self._stop_event.set()
        # Cancel as a backup in case the task is sleeping.
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception) as e:
            # Stop is best-effort — don't propagate shutdown noise.
            _log.debug("worker_stop_swallow", worker=self.name, error=str(e))
        self._task = None

    async def _run(self) -> None:
        # Immediate first tick — admins see effects without delay.
        await self._safe_tick()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
                # If wait returned (no timeout), stop was requested.
                break
            except TimeoutError:
                pass

            await self._safe_tick()

    async def _safe_tick(self) -> None:
        """Run ``tick`` with broad exception suppression + log."""
        try:
            await self.tick()
        except Exception as e:
            _log.exception(
                "worker_tick_failed",
                worker=self.name,
                error=str(e),
            )


__all__ = ["PeriodicWorker"]
