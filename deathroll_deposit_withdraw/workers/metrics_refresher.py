"""Metrics refresher worker (Story 11.1 supporting piece).

Every 30 s, calls ``refresh_from_db`` so the Prometheus Gauges
defined in ``deathroll_deposit_withdraw.metrics`` reflect the
current DB aggregates. Treasury balance, ticket counts by status,
volumes by region, online cashiers, fee revenue, dispute rate per
cashier — all converge on a 30 s lag, which is well under the
typical 1-minute alerting window.

Why a separate worker rather than refreshing inline on every
event: most metrics are aggregates that don't change on every
single mutation, and an event-driven path would couple the metric
updates to the SDF call sites (or worse, require explicit observe()
calls in handlers we don't own). A 30-second poll is simpler and
the staleness is invisible at the dashboard cadence.
"""

from __future__ import annotations

import structlog
from deathroll_core.db import Executor

from deathroll_deposit_withdraw.metrics import refresh_from_db
from deathroll_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


class MetricsRefresherWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`refresh_from_db` every 30 s."""

    def __init__(
        self,
        *,
        pool: Executor,
        interval_seconds: float = 30.0,
    ) -> None:
        super().__init__(name="metrics_refresher", interval_seconds=interval_seconds)
        self._pool = pool

    async def tick(self) -> None:
        await refresh_from_db(pool=self._pool)


__all__ = ["MetricsRefresherWorker"]
