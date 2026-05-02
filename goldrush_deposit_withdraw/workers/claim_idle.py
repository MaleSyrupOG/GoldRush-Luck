"""Claim idle worker (Story 8.2) — auto-release / auto-cancel stale claims.

Spec §4.4 invariant: a claimed ticket must keep moving. Two
deadlines both apply once ``status='claimed'``:

- **30 min idle** (``last_activity_at < NOW() - 30 min``) → auto-release.
  The cashier hasn't typed / clicked anything for half an hour. The
  ticket goes back to ``open`` (FIFO can re-claim it). The cashier
  alert is reposted in ``#cashier-alerts`` so the next cashier sees
  it without waiting for a manual nudge.

- **2 h since claim** (``claimed_at < NOW() - 2 h``) → auto-cancel.
  Hard cap: even if the cashier IS active, two hours is too long
  to hold a ticket. Refunds happen for withdraw tickets via
  ``cancel_withdraw``.

Both deadlines run via dedicated SELECTs so they're auditable in a
``EXPLAIN ANALYZE`` and so the worker reacts to whichever fires
first per ticket. The SECURITY DEFINER fns are idempotent on
``ticket_not_claimed`` / ``ticket_already_terminal`` — a concurrent
admin or the timeout worker (Story 8.1) racing this one resolves
to the same end state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import discord
import structlog
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    cancel_deposit,
    cancel_withdraw,
    release_ticket,
)
from goldrush_core.db import Executor

from goldrush_deposit_withdraw.cashiers.alert import post_cashier_alert
from goldrush_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


_SYSTEM_ACTOR_ID = 0
_REASON_LONG_CLAIM = "auto-cancel: cashier abandoned ticket (>2h since claim)"


@dataclass(frozen=True)
class TickSummary:
    """How many tickets each branch acted on this iteration.

    Surfaces both numbers so observability stories (Story 11.x) can
    emit per-branch metrics without re-instrumenting the worker.
    """

    released: int
    cancelled: int


async def tick(*, pool: Executor, bot: discord.Client) -> TickSummary:
    """Run one pass of the claim-idle worker.

    Two SQL queries, both UNION'd across deposit + withdraw rows so
    one Python loop handles both ticket families.
    """
    released = 0
    cancelled = 0

    # ----------------------------------------------------------------
    # Idle 30 min — auto-release + repost cashier alert.
    # ----------------------------------------------------------------
    idle_rows = await pool.fetch(
        """
        SELECT 'deposit'::TEXT AS ticket_type, ticket_uid, thread_id,
               region, faction, amount
          FROM dw.deposit_tickets
         WHERE status = 'claimed'
           AND last_activity_at < NOW() - INTERVAL '30 minutes'
        UNION ALL
        SELECT 'withdraw'::TEXT AS ticket_type, ticket_uid, thread_id,
               region, faction, amount
          FROM dw.withdraw_tickets
         WHERE status = 'claimed'
           AND last_activity_at < NOW() - INTERVAL '30 minutes'
        """
    )
    for row in idle_rows:
        if await _release_one(pool=pool, bot=bot, row=dict(row)):
            released += 1

    # ----------------------------------------------------------------
    # Claimed > 2h — auto-cancel.
    # ----------------------------------------------------------------
    long_rows = await pool.fetch(
        """
        SELECT 'deposit'::TEXT AS ticket_type, ticket_uid, thread_id,
               region, faction, amount
          FROM dw.deposit_tickets
         WHERE status = 'claimed'
           AND claimed_at < NOW() - INTERVAL '2 hours'
        UNION ALL
        SELECT 'withdraw'::TEXT AS ticket_type, ticket_uid, thread_id,
               region, faction, amount
          FROM dw.withdraw_tickets
         WHERE status = 'claimed'
           AND claimed_at < NOW() - INTERVAL '2 hours'
        """
    )
    for row in long_rows:
        if await _cancel_one(pool=pool, bot=bot, row=dict(row)):
            cancelled += 1

    if released or cancelled:
        _log.info(
            "claim_idle_tick",
            released=released,
            cancelled=cancelled,
        )
    return TickSummary(released=released, cancelled=cancelled)


async def _release_one(
    *,
    pool: Executor,
    bot: discord.Client,
    row: dict[str, object],
) -> bool:
    ticket_type: Literal["deposit", "withdraw"] = (
        "deposit" if row["ticket_type"] == "deposit" else "withdraw"
    )
    ticket_uid = str(row["ticket_uid"])
    try:
        await release_ticket(
            pool,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            actor_id=_SYSTEM_ACTOR_ID,
        )
    except exc.TicketNotClaimed:
        # Already released by cashier or admin — desired state.
        _log.info("claim_idle_already_released", ticket_uid=ticket_uid)
        return False
    except exc.BalanceError as e:
        _log.exception(
            "claim_idle_release_failed",
            ticket_uid=ticket_uid,
            error=e.message,
        )
        return False

    # Repost cashier alert so the next cashier sees it. Best-effort
    # — the cashier-alert poster swallows its own failures.
    region = str(row["region"])
    if region not in ("EU", "NA"):
        return True
    faction = str(row["faction"])
    if faction not in ("Alliance", "Horde"):
        return True
    region_lit: Literal["EU", "NA"] = "EU" if region == "EU" else "NA"
    faction_lit: Literal["Alliance", "Horde"] = (
        "Alliance" if faction == "Alliance" else "Horde"
    )
    amount_obj = row["amount"]
    thread_id_obj = row["thread_id"]
    assert isinstance(amount_obj, int)
    assert isinstance(thread_id_obj, int)
    await post_cashier_alert(
        pool=pool,
        bot=bot,
        ticket_uid=ticket_uid,
        ticket_type=ticket_type,
        region=region_lit,
        faction=faction_lit,
        amount=amount_obj,
        ticket_channel_mention=f"<#{thread_id_obj}>",
    )
    return True


async def _cancel_one(
    *,
    pool: Executor,
    bot: discord.Client,
    row: dict[str, object],
) -> bool:
    ticket_type = row["ticket_type"]
    ticket_uid = str(row["ticket_uid"])
    try:
        if ticket_type == "deposit":
            await cancel_deposit(
                pool,
                ticket_uid=ticket_uid,
                actor_id=_SYSTEM_ACTOR_ID,
                reason=_REASON_LONG_CLAIM,
            )
        else:
            await cancel_withdraw(
                pool,
                ticket_uid=ticket_uid,
                actor_id=_SYSTEM_ACTOR_ID,
                reason=_REASON_LONG_CLAIM,
            )
    except exc.TicketAlreadyTerminal:
        _log.info("claim_idle_already_terminal", ticket_uid=ticket_uid)
        return False
    except exc.BalanceError as e:
        _log.exception(
            "claim_idle_cancel_failed",
            ticket_uid=ticket_uid,
            error=e.message,
        )
        return False
    _ = bot  # bot mention reserved for the audit-log poster (future story).
    return True


class ClaimIdleWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`tick` every 60 s by default."""

    def __init__(
        self,
        *,
        pool: Executor,
        bot: discord.Client,
        interval_seconds: float = 60.0,
    ) -> None:
        super().__init__(name="claim_idle", interval_seconds=interval_seconds)
        self._pool = pool
        self._bot = bot

    async def tick(self) -> None:
        await tick(pool=self._pool, bot=self._bot)


__all__ = ["ClaimIdleWorker", "TickSummary", "tick"]
