"""Python facade over the dw.* SECURITY DEFINER functions.

These wrappers call the SECURITY DEFINER functions defined in the Alembic
migrations 0006-0012 and translate the Postgres ``RaiseError`` exceptions
they emit into typed Python exceptions from
``goldrush_core.balance.exceptions``.

The bot's slash-command handlers should ALWAYS call these wrappers
rather than constructing raw SQL, because:

1. The wrappers carry a stable typed interface. Behaviour changes that
   touch the SQL contract (new sentinel, signature change) ripple through
   here and become loud type errors at the call site.
2. The translation step turns Postgres exceptions into rich Python
   exceptions usable in handler logic (e.g. ``except InsufficientBalance:
   await interaction.response.send_message(insufficient_balance_embed)``).
3. Keeping every economic call in one Python file makes the audit trail
   readable: every place balance can move is here, and every place this
   file is imported is in the call graph.

Each wrapper accepts an ``Executor`` — anything with the asyncpg query
methods we use (``Pool``, ``Connection``, ``PoolConnectionProxy``). For
single operations a Pool is fine; for multi-step orchestration, callers
should ``async with pool.acquire() as conn`` and pass the connection.
"""

from __future__ import annotations

from typing import Literal

import asyncpg

from goldrush_core.balance.exceptions import translate_pg_error
from goldrush_core.db import Executor


# ---------------------------------------------------------------------------
# Deposit lifecycle (migration 0006_dw_deposit_fns)
# ---------------------------------------------------------------------------


async def apply_deposit_ticket(
    conn: Executor,
    *,
    discord_id: int,
    char_name: str,
    realm: str,
    region: Literal["EU", "NA"],
    faction: Literal["Alliance", "Horde"],
    amount: int,
    thread_id: int,
    parent_channel_id: int,
) -> str:
    """Open a deposit ticket. Returns the ticket_uid (e.g. ``deposit-1``).

    Notes:
    - The ``thread_id`` parameter is named for the legacy private-thread
      design; on migration to private channels it carries the channel id.
    - No balance change happens here. The user has not yet sent gold;
      the cashier will trigger the actual credit at confirm time.

    Raises:
        AmountOutOfRange, InvalidRegion, InvalidFaction, GlobalConfigMissing.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.create_deposit_ticket($1, $2, $3, $4, $5, $6, $7, $8)",
            discord_id,
            char_name,
            realm,
            region,
            faction,
            amount,
            thread_id,
            parent_channel_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def confirm_deposit(
    conn: Executor,
    *,
    ticket_uid: str,
    cashier_id: int,
) -> int:
    """Confirm a claimed deposit. Returns the user's new balance after credit.

    Idempotent on retry: if the ticket is already confirmed by this cashier,
    returns the current balance without reapplying the credit.

    Raises:
        TicketNotFound, TicketNotClaimed, WrongCashier.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.confirm_deposit($1, $2)",
            ticket_uid,
            cashier_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def cancel_deposit(
    conn: Executor,
    *,
    ticket_uid: str,
    actor_id: int,
    reason: str,
) -> None:
    """Cancel an open or claimed deposit. No balance change.

    Raises:
        TicketNotFound, TicketAlreadyTerminal.
    """
    try:
        await conn.execute(
            "SELECT dw.cancel_deposit($1, $2, $3)",
            ticket_uid,
            actor_id,
            reason,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


# ---------------------------------------------------------------------------
# Withdraw lifecycle (migration 0007_dw_withdraw_fns)
# ---------------------------------------------------------------------------


async def apply_withdraw_ticket(
    conn: Executor,
    *,
    discord_id: int,
    char_name: str,
    realm: str,
    region: Literal["EU", "NA"],
    faction: Literal["Alliance", "Horde"],
    amount: int,
    thread_id: int,
    parent_channel_id: int,
) -> str:
    """Open a withdraw ticket. Locks the requested amount on the user's balance.

    The fee captured at creation time is the current value of
    ``dw.global_config.withdraw_fee_bps``; subsequent rate changes do not
    affect this ticket's fee.

    Raises:
        AmountOutOfRange, InvalidRegion, InvalidFaction, UserNotRegistered,
        UserBanned, InsufficientBalance.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.create_withdraw_ticket($1, $2, $3, $4, $5, $6, $7, $8)",
            discord_id,
            char_name,
            realm,
            region,
            faction,
            amount,
            thread_id,
            parent_channel_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def confirm_withdraw(
    conn: Executor,
    *,
    ticket_uid: str,
    cashier_id: int,
) -> int:
    """Confirm a claimed withdraw. Treasury gains the fee; user loses the gross.

    Returns the user's balance from BEFORE confirm completes (the lock
    has already deducted; this just finalises). For a full balance probe,
    select ``balance`` after this call.

    Raises:
        TicketNotFound, TicketNotClaimed, WrongCashier, InvariantViolation.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.confirm_withdraw($1, $2)",
            ticket_uid,
            cashier_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def cancel_withdraw(
    conn: Executor,
    *,
    ticket_uid: str,
    actor_id: int,
    reason: str,
) -> int:
    """Cancel a withdraw and refund the locked amount. Returns the user's new balance.

    Raises:
        TicketNotFound, TicketAlreadyTerminal, InvariantViolation.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.cancel_withdraw($1, $2, $3)",
            ticket_uid,
            actor_id,
            reason,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


# ---------------------------------------------------------------------------
# Treasury (migration 0011_dw_treasury_fns)
# ---------------------------------------------------------------------------


async def treasury_sweep(
    conn: Executor,
    *,
    amount: int,
    admin_id: int,
    reason: str,
) -> int:
    """Record an admin physically removing gold from the in-game guild bank.

    Returns the treasury balance after the debit.

    Raises:
        AmountMustBePositive, TreasuryRowMissing, InsufficientTreasury.
    """
    try:
        return await conn.fetchval(
            "SELECT dw.treasury_sweep($1, $2, $3)",
            amount,
            admin_id,
            reason,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def treasury_withdraw_to_user(
    conn: Executor,
    *,
    amount: int,
    target_user: int,
    admin_id: int,
    reason: str,
) -> None:
    """Move gold from the treasury to a real user (refund / dispute path).

    Raises:
        AmountMustBePositive, CannotWithdrawToTreasurySelf, TreasuryRowMissing,
        InsufficientTreasury.
    """
    try:
        await conn.execute(
            "SELECT dw.treasury_withdraw_to_user($1, $2, $3, $4)",
            amount,
            target_user,
            admin_id,
            reason,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e
