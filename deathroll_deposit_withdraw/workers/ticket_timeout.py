"""Ticket timeout worker (Story 8.1) — expire stale tickets.

Spec §4.4 says the bot maintains four lifecycle invariants via
background workers; this module owns invariant #1: any ticket whose
``expires_at`` is in the past must be moved to a terminal state
(``cancelled``) regardless of who left it open.

Two ticket families are watched: ``dw.deposit_tickets`` and
``dw.withdraw_tickets``. For each, we look at ``status IN
('open','claimed') AND expires_at < NOW()`` and call the canonical
SECURITY DEFINER cancel fn (``dw.cancel_deposit`` /
``dw.cancel_withdraw``) with the system actor (``actor_id=0``) and a
reason starting with ``auto-cancel: expired`` so the audit row is
self-explanatory.

Idempotent across crashes: if a worker is killed mid-loop, the next
iteration sees the same expired tickets and finishes cancelling
them. If a concurrent admin force-cancel beats the worker to it,
the SECURITY DEFINER fn raises ``ticket_already_terminal`` which the
worker swallows (the ticket is in the desired state anyway).

Story 8.1 AC: claimed-side cancellations also surface to admins via
the ``#audit-log`` channel poster (``audit_ticket_cancelled`` with a
``System`` actor mention) so admins notice cashiers who let claims
go stale until the timeout fired.
"""

from __future__ import annotations

import discord
import structlog
from deathroll_core.balance import exceptions as exc
from deathroll_core.balance.dw_manager import cancel_deposit, cancel_withdraw
from deathroll_core.db import Executor

from deathroll_deposit_withdraw.audit_log import audit_ticket_cancelled
from deathroll_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


_SYSTEM_ACTOR_ID = 0
_REASON_OPEN = "auto-cancel: expired (open ticket)"
_REASON_CLAIMED = "auto-cancel: expired (claimed ticket left stale)"


async def tick(*, pool: Executor, bot: discord.Client) -> int:
    """Run one pass of the timeout worker. Returns the cancel count.

    Pulls every expired ticket from BOTH tables in a single iteration
    so a backlog after a downtime window converges in one tick. The
    cancel SDF acquires its own row lock so concurrent workers /
    admins are safe.
    """
    cancelled = 0

    expired_deposits = await pool.fetch(
        """
        SELECT ticket_uid, status, discord_id, amount
          FROM dw.deposit_tickets
         WHERE status IN ('open', 'claimed')
           AND expires_at < NOW()
         ORDER BY expires_at ASC
        """
    )
    for row in expired_deposits:
        if await _cancel_one(
            pool=pool,
            bot=bot,
            kind="deposit",
            ticket_uid=str(row["ticket_uid"]),
            status=str(row["status"]),
            discord_id=int(row["discord_id"]),
            amount=int(row["amount"]),
        ):
            cancelled += 1

    expired_withdraws = await pool.fetch(
        """
        SELECT ticket_uid, status, discord_id, amount
          FROM dw.withdraw_tickets
         WHERE status IN ('open', 'claimed')
           AND expires_at < NOW()
         ORDER BY expires_at ASC
        """
    )
    for row in expired_withdraws:
        if await _cancel_one(
            pool=pool,
            bot=bot,
            kind="withdraw",
            ticket_uid=str(row["ticket_uid"]),
            status=str(row["status"]),
            discord_id=int(row["discord_id"]),
            amount=int(row["amount"]),
        ):
            cancelled += 1

    if cancelled:
        _log.info("ticket_timeout_tick", cancelled=cancelled)
    return cancelled


async def _cancel_one(
    *,
    pool: Executor,
    bot: discord.Client,
    kind: str,
    ticket_uid: str,
    status: str,
    discord_id: int,
    amount: int,
) -> bool:
    """Cancel a single expired ticket. Returns True if the cancel
    actually moved state, False on idempotent already-terminal.
    """
    reason = _REASON_CLAIMED if status == "claimed" else _REASON_OPEN
    try:
        if kind == "deposit":
            await cancel_deposit(
                pool,
                ticket_uid=ticket_uid,
                actor_id=_SYSTEM_ACTOR_ID,
                reason=reason,
            )
        else:
            await cancel_withdraw(
                pool,
                ticket_uid=ticket_uid,
                actor_id=_SYSTEM_ACTOR_ID,
                reason=reason,
            )
    except exc.TicketAlreadyTerminal:
        # Concurrent worker / admin already handled it — desired state.
        _log.info(
            "ticket_timeout_already_terminal",
            ticket_uid=ticket_uid,
        )
        return False
    except exc.BalanceError as e:
        _log.exception(
            "ticket_timeout_cancel_failed",
            ticket_uid=ticket_uid,
            error=e.message,
        )
        return False

    # Best-effort audit-log post: a ``claimed`` ticket timing out is
    # the operationally interesting one (the cashier ghosted), so we
    # surface it to admins. ``open`` timeouts are routine (user
    # opened then walked away) and only flood the channel if posted.
    if status == "claimed":
        await audit_ticket_cancelled(
            pool=pool,
            bot=bot,
            ticket_type=kind,  # type: ignore[arg-type]
            ticket_uid=ticket_uid,
            actor_mention="**System**",
            reason=reason,
        )
    _ = (discord_id, amount)  # surfaced via the SDF's audit row, not here.
    return True


class TicketTimeoutWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`tick` every 60 s by default."""

    def __init__(
        self,
        *,
        pool: Executor,
        bot: discord.Client,
        interval_seconds: float = 60.0,
    ) -> None:
        super().__init__(name="ticket_timeout", interval_seconds=interval_seconds)
        self._pool = pool
        self._bot = bot

    async def tick(self) -> None:
        await tick(pool=self._pool, bot=self._bot)


__all__ = ["TicketTimeoutWorker", "tick"]
