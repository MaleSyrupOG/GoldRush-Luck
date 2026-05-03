"""Unit tests for the ``PeriodicWorker`` base class (Epic 8 scaffolding).

Five of the six Epic 8 stories all share one shape — an asyncio loop
that runs ``tick()`` every N seconds, with idempotent ``start()`` and
awaitable ``stop()``. We factor the common scaffolding into
``goldrush_deposit_withdraw.workers._periodic.PeriodicWorker`` so each
worker only writes its ``tick`` body.

Tests run with VERY short intervals (sub-second) so we exercise the
cancellation + iteration paths without a slow suite.
"""

from __future__ import annotations

import asyncio

import pytest
from goldrush_deposit_withdraw.workers._periodic import PeriodicWorker


class _Counter(PeriodicWorker):
    """Test-only worker that increments on each tick.

    Exposes ``ticks`` as the count and ``crash_after`` to verify the
    loop survives a tick that raises (broad-except in the loop).
    """

    def __init__(
        self, *, interval: float = 0.05, crash_after: int | None = None
    ) -> None:
        super().__init__(name="counter", interval_seconds=interval)
        self.ticks = 0
        self._crash_after = crash_after

    async def tick(self) -> None:
        self.ticks += 1
        if self._crash_after is not None and self.ticks == self._crash_after:
            raise RuntimeError("forced crash for test")


@pytest.mark.asyncio
async def test_worker_runs_initial_tick_then_periodically() -> None:
    """First ``tick`` happens immediately on ``start()``, subsequent
    ticks happen every ``interval_seconds`` until ``stop()``."""
    worker = _Counter(interval=0.05)
    worker.start()
    # Wait long enough to see at least 3 ticks (initial + 2 wakeups).
    await asyncio.sleep(0.18)
    await worker.stop()
    assert worker.ticks >= 3


@pytest.mark.asyncio
async def test_worker_start_is_idempotent() -> None:
    """``start()`` while already running is a no-op (no second loop)."""
    worker = _Counter(interval=0.05)
    worker.start()
    worker.start()  # no-op
    await asyncio.sleep(0.12)
    await worker.stop()
    assert worker.ticks <= 4  # not double-rate; would be ~6+ if duplicated


@pytest.mark.asyncio
async def test_worker_stop_waits_for_tick_loop_to_exit() -> None:
    """After ``stop()`` returns, no further ticks happen."""
    worker = _Counter(interval=0.05)
    worker.start()
    await asyncio.sleep(0.12)
    await worker.stop()
    snapshot = worker.ticks
    await asyncio.sleep(0.15)
    assert worker.ticks == snapshot


@pytest.mark.asyncio
async def test_worker_loop_survives_tick_exception() -> None:
    """An exception in ``tick`` is logged + swallowed; loop keeps running."""
    worker = _Counter(interval=0.04, crash_after=2)
    worker.start()
    await asyncio.sleep(0.18)
    await worker.stop()
    # Despite tick #2 raising, the loop continued to tick 3, 4, ...
    assert worker.ticks >= 3
