"""Stats aggregator worker (Story 8.5).

Every 15 min, recomputes the two derived columns on
``dw.cashier_stats``:

- ``avg_claim_to_confirm_s`` — moving average (in seconds) of
  ``confirmed_at - claimed_at`` over the cashier's most recent 100
  confirmations across BOTH ``dw.deposit_tickets`` and
  ``dw.withdraw_tickets``. Surfaces in ``/cashier-mystats`` and
  ``/admin-cashier-stats``.

- ``total_online_seconds`` — SUM of ``duration_s`` from the
  cashier's ``dw.cashier_sessions`` rows. The session writer in
  ``set_cashier_status`` and ``expire_cashier`` (Story 8.3)
  populates ``duration_s`` on session close, so this column is the
  ground truth for "how much have they been online".

Plain SQL: no SECURITY DEFINER fn because the bot's
``deathroll_dw`` role already has SELECT on tickets / sessions and
UPDATE on ``cashier_stats`` per migration 0004's GRANT block. One
UPDATE per cashier — for ten cashiers this is ten round-trips, for
hundreds it would still be < 1 s every 15 min so we don't optimise
prematurely.

Idempotency: the UPDATE recomputes from scratch every iteration so
re-running after a crash converges to the right value. There is
no "since last run" pointer to advance — that's a deliberate
choice (the spec mentions cashiers with new confirmations as the
target set, but the cost difference is negligible at our scale).
"""

from __future__ import annotations

import structlog
from deathroll_core.db import Executor

from deathroll_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


_RECOMPUTE_SQL = """
UPDATE dw.cashier_stats
   SET avg_claim_to_confirm_s = (
        SELECT AVG(seconds)::INTEGER
          FROM (
            SELECT EXTRACT(EPOCH FROM (confirmed_at - claimed_at)) AS seconds,
                   confirmed_at
              FROM (
                SELECT confirmed_at, claimed_at
                  FROM dw.deposit_tickets
                 WHERE claimed_by = $1
                   AND status = 'confirmed'
                   AND confirmed_at IS NOT NULL
                   AND claimed_at  IS NOT NULL
                UNION ALL
                SELECT confirmed_at, claimed_at
                  FROM dw.withdraw_tickets
                 WHERE claimed_by = $1
                   AND status = 'confirmed'
                   AND confirmed_at IS NOT NULL
                   AND claimed_at  IS NOT NULL
              ) confs
             ORDER BY confirmed_at DESC
             LIMIT 100
          ) recent
       ),
       total_online_seconds = COALESCE((
           SELECT SUM(duration_s)
             FROM dw.cashier_sessions
            WHERE discord_id = $1
              AND duration_s IS NOT NULL
       ), 0),
       updated_at = NOW()
 WHERE discord_id = $1
"""


async def tick(*, pool: Executor) -> int:
    """Run one pass — returns how many cashier_stats rows were updated."""
    rows = await pool.fetch(
        "SELECT discord_id FROM dw.cashier_stats ORDER BY discord_id ASC"
    )
    updated = 0
    for row in rows:
        discord_id = int(row["discord_id"])
        try:
            await pool.execute(_RECOMPUTE_SQL, discord_id)
            updated += 1
        except Exception as e:
            _log.exception(
                "stats_aggregator_update_failed",
                discord_id=discord_id,
                error=str(e),
            )
    if updated:
        _log.info("stats_aggregator_tick", updated=updated)
    return updated


class StatsAggregatorWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`tick` every 15 min by default."""

    def __init__(
        self,
        *,
        pool: Executor,
        interval_seconds: float = 900.0,  # 15 minutes
    ) -> None:
        super().__init__(name="stats_aggregator", interval_seconds=interval_seconds)
        self._pool = pool

    async def tick(self) -> None:
        await tick(pool=self._pool)


__all__ = ["StatsAggregatorWorker", "tick"]
