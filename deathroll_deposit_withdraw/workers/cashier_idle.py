"""Cashier idle worker (Story 8.3) — auto-offline stale cashiers.

Every 5 min, scans ``dw.cashier_status`` for rows in
``status='online'`` whose ``last_active_at`` is more than 1 h old
and flips them to offline via ``dw.expire_cashier`` (closes the
session row with ``end_reason='expired'``).

Why a separate SDF instead of reusing ``dw.set_cashier_status``: the
latter hardcodes ``end_reason='manual_offline'``, which would
muddy the audit trail. Splitting the verb keeps the intent clear
across both the audit_log row and the
``dw.cashier_sessions.end_reason`` field, so reporting against
"how long do cashiers actually keep themselves online vs. how often
do they idle out" is a single GROUP BY.

Idempotency: the SDF raises ``cashier_not_online`` when a manual
``/cashier-offline`` (or another worker iteration) beats it. The
worker swallows. Subsequent ticks see the same predicate and
either find new candidates or do nothing.
"""

from __future__ import annotations

import structlog
from deathroll_core.balance import exceptions as exc
from deathroll_core.balance.dw_manager import expire_cashier
from deathroll_core.db import Executor

from deathroll_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


async def tick(*, pool: Executor) -> int:
    """Run one pass — returns how many cashiers were auto-offlined."""
    # Idle threshold is fixed at 1 hour per spec §4.4 — hardcoding it
    # in the query (rather than passing as a parameter) keeps the
    # SELECT planner-friendly and matches the SDF's contract.
    rows = await pool.fetch(
        """
        SELECT discord_id
          FROM dw.cashier_status
         WHERE status = 'online'
           AND last_active_at < NOW() - INTERVAL '1 hour'
         ORDER BY last_active_at ASC
        """
    )
    expired = 0
    for row in rows:
        discord_id = int(row["discord_id"])
        try:
            await expire_cashier(pool, discord_id=discord_id)
            expired += 1
        except exc.CashierNotOnline:
            _log.info("cashier_idle_already_offline", discord_id=discord_id)
        except exc.BalanceError as e:
            _log.exception(
                "cashier_idle_expire_failed",
                discord_id=discord_id,
                error=e.message,
            )
    if expired:
        _log.info("cashier_idle_tick", expired=expired)
    return expired


class CashierIdleWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`tick` every 5 min by default."""

    def __init__(
        self,
        *,
        pool: Executor,
        interval_seconds: float = 300.0,
    ) -> None:
        super().__init__(name="cashier_idle", interval_seconds=interval_seconds)
        self._pool = pool

    async def tick(self) -> None:
        await tick(pool=self._pool)


__all__ = ["CashierIdleWorker", "tick"]
