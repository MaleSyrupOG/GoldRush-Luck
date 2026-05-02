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

from typing import Literal, cast

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
        result = await conn.fetchval(
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
        return cast(str, result)
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
        result = await conn.fetchval(
            "SELECT dw.confirm_deposit($1, $2)",
            ticket_uid,
            cashier_id,
        )
        return cast(int, result)
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
# Lifecycle (migration 0008_dw_lifecycle_fns) — claim / release
# Generic across ticket types; the SECURITY DEFINER fn dispatches on
# ``ticket_type`` to update the right table and writes the audit row.
# ---------------------------------------------------------------------------


async def claim_ticket(
    conn: Executor,
    *,
    ticket_type: Literal["deposit", "withdraw"],
    ticket_uid: str,
    cashier_id: int,
) -> None:
    """Cashier claims an open ticket.

    Raises:
        InvalidTicketType, TicketNotFound, TicketAlreadyClaimed,
        RegionMismatch (cashier has no active char in the ticket's region).
    """
    try:
        await conn.execute(
            "SELECT dw.claim_ticket($1, $2, $3)",
            ticket_type,
            ticket_uid,
            cashier_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def release_ticket(
    conn: Executor,
    *,
    ticket_type: Literal["deposit", "withdraw"],
    ticket_uid: str,
    actor_id: int,
) -> None:
    """Cashier voluntarily releases a claimed ticket back to ``open``.

    The withdraw fn does NOT release the locked balance — that only
    happens on cancel; release just hands the ticket back to FIFO.

    Raises:
        InvalidTicketType, TicketNotFound, TicketNotClaimed, WrongCashier.
    """
    try:
        await conn.execute(
            "SELECT dw.release_ticket($1, $2, $3)",
            ticket_type,
            ticket_uid,
            actor_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


# ---------------------------------------------------------------------------
# Cashier system (migration 0009_dw_cashier_fns)
# ---------------------------------------------------------------------------


async def add_cashier_character(
    conn: Executor,
    *,
    discord_id: int,
    char: str,
    realm: str,
    region: Literal["EU", "NA"],
    faction: Literal["Alliance", "Horde"],
) -> int:
    """Register a (char, realm, region, faction) tuple to the cashier.

    Returns the new ``dw.cashier_characters.id``. Re-registering an
    existing (cashier, char, realm) reactivates a soft-deleted row
    in the same migration's logic; no Python work needed here.

    Raises:
        InvalidRegion, InvalidFaction.
    """
    try:
        result = await conn.fetchval(
            "SELECT dw.add_cashier_character($1, $2, $3, $4, $5)",
            discord_id,
            char,
            realm,
            region,
            faction,
        )
        return cast(int, result)
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def remove_cashier_character(
    conn: Executor,
    *,
    discord_id: int,
    char: str,
    realm: str,
    region: Literal["EU", "NA"],
) -> None:
    """Soft-remove a cashier's character from the active roster.

    Raises:
        CharacterNotFoundOrAlreadyRemoved, InvalidRegion.
    """
    try:
        await conn.execute(
            "SELECT dw.remove_cashier_character($1, $2, $3, $4)",
            discord_id,
            char,
            realm,
            region,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def set_cashier_status(
    conn: Executor,
    *,
    discord_id: int,
    status: Literal["online", "offline", "break"],
) -> None:
    """Toggle the cashier's roster status.

    The SECURITY DEFINER fn handles the ``dw.cashier_sessions``
    bookkeeping — opens a row on online, closes the open row on
    offline / break.

    Raises:
        InvalidStatus.
    """
    try:
        await conn.execute(
            "SELECT dw.set_cashier_status($1, $2)",
            discord_id,
            status,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def expire_cashier(
    conn: Executor,
    *,
    discord_id: int,
) -> None:
    """Auto-offline an idle cashier (Story 8.3).

    Distinct from ``set_cashier_status(status='offline')``: this fn
    closes the ``cashier_sessions`` row with ``end_reason='expired'``
    and writes a ``cashier_status_offline_expired`` audit row. The
    cashier-idle worker calls this every 5 min for any cashier
    online and idle >1h.

    Raises:
        CashierNotOnline — the cashier is no longer online (caller
        swallows; race with manual ``/cashier-offline`` resolves to
        the desired state).
    """
    try:
        await conn.execute(
            "SELECT dw.expire_cashier($1)",
            discord_id,
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
        result = await conn.fetchval(
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
        return cast(str, result)
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
        result = await conn.fetchval(
            "SELECT dw.confirm_withdraw($1, $2)",
            ticket_uid,
            cashier_id,
        )
        return cast(int, result)
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
        result = await conn.fetchval(
            "SELECT dw.cancel_withdraw($1, $2, $3)",
            ticket_uid,
            actor_id,
            reason,
        )
        return cast(int, result)
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
        result = await conn.fetchval(
            "SELECT dw.treasury_sweep($1, $2, $3)",
            amount,
            admin_id,
            reason,
        )
        return cast(int, result)
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


# ---------------------------------------------------------------------------
# Disputes (migrations 0010_dw_dispute_fns + 0013_dw_dispute_reject_fn)
# ---------------------------------------------------------------------------


async def open_dispute(
    conn: Executor,
    *,
    ticket_type: Literal["deposit", "withdraw"],
    ticket_uid: str,
    opener_id: int,
    opener_role: Literal["admin", "user", "system"],
    reason: str,
) -> int:
    """Open a new dispute on a ticket. Returns the new ``dw.disputes.id``.

    The SQL fn enforces:
    - ``ticket_type`` is in {deposit, withdraw};
    - ``opener_role`` is in {admin, user, system};
    - ``ticket_uid`` exists in the matching tickets table;
    - At most one open dispute per ticket (UNIQUE constraint).

    Raises:
        InvalidTicketType, InvalidOpenerRole, TicketNotFound, plus a
        unique-violation if a dispute is already open on the ticket
        (translated as a generic BalanceError).
    """
    try:
        result = await conn.fetchval(
            "SELECT dw.open_dispute($1, $2, $3, $4, $5)",
            ticket_type,
            ticket_uid,
            opener_id,
            opener_role,
            reason,
        )
        return cast(int, result)
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def resolve_dispute(
    conn: Executor,
    *,
    dispute_id: int,
    action: Literal["no-action", "refund-full", "force-confirm", "partial-refund"],
    amount: int | None,
    resolved_by: int,
) -> None:
    """Resolve an open dispute with one of four outcomes.

    Action semantics (all written by the SQL fn):
    - ``no-action``       — close as resolved, no money moves.
    - ``force-confirm``   — closed without money flow (used when a
                            cancelled deposit was actually completed
                            in-game and we accept the cashier's word).
    - ``refund-full``     — only valid for withdraw disputes; moves the
                            full ticket amount from treasury to user.
    - ``partial-refund``  — moves ``amount`` G from treasury to user.

    Raises:
        DisputeNotFound, DisputeAlreadyTerminal, InvalidAction,
        PartialRefundRequiresPositiveAmount, RefundFullOnlyForWithdrawDisputes,
        plus the treasury-side errors when refund routes activate.
    """
    try:
        await conn.execute(
            "SELECT dw.resolve_dispute($1, $2, $3, $4)",
            dispute_id,
            action,
            amount,
            resolved_by,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def reject_dispute(
    conn: Executor,
    *,
    dispute_id: int,
    reason: str,
    admin_id: int,
) -> None:
    """Reject (close-without-resolution) a dispute. No money moves.

    Distinct from ``resolve_dispute``: this is the verb the admin uses
    when siding AGAINST the opener — the dispute closes with
    ``status='rejected'`` and the audit row reads ``dispute_rejected``.

    Raises:
        DisputeNotFound, DisputeAlreadyTerminal.
    """
    try:
        await conn.execute(
            "SELECT dw.reject_dispute($1, $2, $3)",
            dispute_id,
            reason,
            admin_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


# ---------------------------------------------------------------------------
# Blacklist (migration 0012_dw_ban_fns)
# ---------------------------------------------------------------------------


async def ban_user(
    conn: Executor,
    *,
    user_id: int,
    reason: str,
    admin_id: int,
) -> None:
    """Mark a user as banned (``core.users.banned = TRUE``).

    The SECURITY DEFINER fn idempotently inserts the user row if absent
    so admins can pre-emptively ban a known-bad Discord ID before that
    user has ever interacted with the bot.

    Raises:
        CannotBanTreasury — when ``user_id == 0`` (the treasury seed
        row, which must never be banned because every refund passes
        through it).
    """
    try:
        await conn.execute(
            "SELECT dw.ban_user($1, $2, $3)",
            user_id,
            reason,
            admin_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e


async def unban_user(
    conn: Executor,
    *,
    user_id: int,
    admin_id: int,
) -> None:
    """Reverse a ban. Clears banned / banned_reason / banned_at.

    Raises:
        UserNotRegistered — the user has no row in ``core.users``
        (never deposited and was never pre-emptively banned).
    """
    try:
        await conn.execute(
            "SELECT dw.unban_user($1, $2)",
            user_id,
            admin_id,
        )
    except asyncpg.RaiseError as e:
        raise translate_pg_error(e) from e
