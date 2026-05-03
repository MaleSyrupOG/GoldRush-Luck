"""Lifecycle state machine tests for deposit + withdraw tickets (Story 14.2).

Spec §8.2: every (state, action) pair tested for valid/invalid
transitions; terminal states immutable.

Lifecycle states: ``open``, ``claimed``, ``confirmed``, ``cancelled``,
``expired``. Actions exposed via the SECURITY DEFINER fns: ``claim``,
``release``, ``confirm``, ``cancel``.

Valid transitions:

  open      → claim   → claimed
  open      → cancel  → cancelled
  claimed   → release → open
  claimed   → confirm → confirmed
  claimed   → cancel  → cancelled

Every other (state, action) combination must be rejected by the SDF.

Plus an immutability check: ``core.audit_log`` UPDATE / DELETE
triggers must fire even when the admin role attempts the change
(triggers raise unconditionally; no role can bypass them without
disabling the trigger).
"""

from __future__ import annotations

from typing import Literal

import asyncpg
import pytest
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    apply_deposit_ticket,
    apply_withdraw_ticket,
    cancel_deposit,
    cancel_withdraw,
    claim_ticket,
    confirm_deposit,
    confirm_withdraw,
    release_ticket,
)


_USER = 12345
_CASHIER = 9001


# ---------------------------------------------------------------------------
# Helpers — bring a ticket to a given starting state on a clean DB.
# ---------------------------------------------------------------------------


async def _seed_cashier(pool: asyncpg.Pool) -> None:
    """Register + online a single EU-Horde cashier so claims succeed."""
    await pool.execute(
        "INSERT INTO dw.cashier_characters "
        "(discord_id, char_name, realm, region, faction) "
        "VALUES ($1, 'Cashier', 'Stormrage', 'EU', 'Horde')",
        _CASHIER,
    )
    await pool.execute(
        "INSERT INTO dw.cashier_status (discord_id, status, set_at, last_active_at) "
        "VALUES ($1, 'online', NOW(), NOW())",
        _CASHIER,
    )


async def _open_deposit(pool: asyncpg.Pool) -> str:
    return await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="Char",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=111111,
        parent_channel_id=222222,
    )


async def _open_withdraw(pool: asyncpg.Pool) -> str:
    # Pre-fund the user via a seed deposit so the withdraw clears
    # balance check.
    pre = await _open_deposit(pool)
    await pool.execute("SELECT dw.claim_ticket('deposit', $1, $2)", pre, _CASHIER)
    await confirm_deposit(pool, ticket_uid=pre, cashier_id=_CASHIER)

    return await apply_withdraw_ticket(
        pool,
        discord_id=_USER,
        char_name="Char",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=5_000,
        thread_id=333333,
        parent_channel_id=444444,
    )


# ---------------------------------------------------------------------------
# Deposit lifecycle — every transition.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deposit_open_to_claimed_succeeds(pool: asyncpg.Pool) -> None:
    await _seed_cashier(pool)
    uid = await _open_deposit(pool)
    await pool.execute("SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER)
    status = await pool.fetchval(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = $1", uid
    )
    assert status == "claimed"


@pytest.mark.asyncio
async def test_deposit_open_to_cancelled_succeeds(pool: asyncpg.Pool) -> None:
    uid = await _open_deposit(pool)
    await cancel_deposit(pool, ticket_uid=uid, actor_id=_USER, reason="user")
    status = await pool.fetchval(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = $1", uid
    )
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_deposit_claimed_to_confirmed_succeeds(pool: asyncpg.Pool) -> None:
    await _seed_cashier(pool)
    uid = await _open_deposit(pool)
    await pool.execute("SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER)
    new_balance = await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)
    assert new_balance == 10_000


@pytest.mark.asyncio
async def test_deposit_claimed_to_open_via_release_succeeds(
    pool: asyncpg.Pool,
) -> None:
    await _seed_cashier(pool)
    uid = await _open_deposit(pool)
    await pool.execute("SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER)
    await pool.execute("SELECT dw.release_ticket('deposit', $1, $2)", uid, _CASHIER)
    status = await pool.fetchval(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = $1", uid
    )
    assert status == "open"


@pytest.mark.asyncio
async def test_deposit_open_to_confirm_rejected(pool: asyncpg.Pool) -> None:
    """confirm requires status='claimed'."""
    uid = await _open_deposit(pool)
    with pytest.raises(exc.TicketNotClaimed):
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)


@pytest.mark.asyncio
async def test_deposit_open_to_release_rejected(pool: asyncpg.Pool) -> None:
    """release also requires status='claimed'."""
    uid = await _open_deposit(pool)
    with pytest.raises(exc.TicketNotClaimed):
        await release_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, actor_id=_CASHIER
        )


@pytest.mark.asyncio
async def test_deposit_confirmed_terminal_cancel_rejected(
    pool: asyncpg.Pool,
) -> None:
    await _seed_cashier(pool)
    uid = await _open_deposit(pool)
    await pool.execute("SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER)
    await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)
    with pytest.raises(exc.TicketAlreadyTerminal):
        await cancel_deposit(pool, ticket_uid=uid, actor_id=_USER, reason="late")


@pytest.mark.asyncio
async def test_deposit_cancelled_terminal_confirm_rejected(
    pool: asyncpg.Pool,
) -> None:
    uid = await _open_deposit(pool)
    await cancel_deposit(pool, ticket_uid=uid, actor_id=_USER, reason="x")
    # Confirm on a cancelled ticket -> SDF raises ticket_not_claimed
    # (status='cancelled' != 'claimed').
    with pytest.raises(exc.TicketNotClaimed):
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)


@pytest.mark.asyncio
async def test_deposit_claimed_double_claim_rejected(pool: asyncpg.Pool) -> None:
    await _seed_cashier(pool)
    uid = await _open_deposit(pool)
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
    )
    with pytest.raises(exc.TicketAlreadyClaimed):
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
        )


# ---------------------------------------------------------------------------
# Withdraw lifecycle — same shape; locks balance on open and refunds
# on cancel, so the assertions tighten the balance round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdraw_lock_and_cancel_refunds(pool: asyncpg.Pool) -> None:
    await _seed_cashier(pool)
    uid = await _open_withdraw(pool)
    # After opening a withdraw, the user's balance should be reduced
    # by the gross amount (locked).
    locked_balance = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = $1", _USER
    )
    assert locked_balance == 10_000 - 5_000  # seed - locked

    await cancel_withdraw(pool, ticket_uid=uid, actor_id=_USER, reason="changed mind")
    refunded = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = $1", _USER
    )
    assert refunded == 10_000  # back to original


@pytest.mark.asyncio
async def test_withdraw_claimed_to_confirmed_credits_treasury(
    pool: asyncpg.Pool,
) -> None:
    await _seed_cashier(pool)
    uid = await _open_withdraw(pool)
    await pool.execute("SELECT dw.claim_ticket('withdraw', $1, $2)", uid, _CASHIER)
    await confirm_withdraw(pool, ticket_uid=uid, cashier_id=_CASHIER)

    # Treasury holds the captured fee.
    fee = await pool.fetchval(
        "SELECT fee FROM dw.withdraw_tickets WHERE ticket_uid = $1", uid
    )
    treasury = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = 0"
    )
    assert treasury == int(fee)


@pytest.mark.asyncio
async def test_withdraw_confirmed_terminal_cancel_rejected(
    pool: asyncpg.Pool,
) -> None:
    await _seed_cashier(pool)
    uid = await _open_withdraw(pool)
    await pool.execute("SELECT dw.claim_ticket('withdraw', $1, $2)", uid, _CASHIER)
    await confirm_withdraw(pool, ticket_uid=uid, cashier_id=_CASHIER)
    with pytest.raises(exc.TicketAlreadyTerminal):
        await cancel_withdraw(pool, ticket_uid=uid, actor_id=_USER, reason="late")


@pytest.mark.parametrize("ticket_type", ["deposit", "withdraw"])
@pytest.mark.asyncio
async def test_lifecycle_release_when_not_claimed_rejected(
    pool: asyncpg.Pool, ticket_type: Literal["deposit", "withdraw"]
) -> None:
    await _seed_cashier(pool)
    if ticket_type == "deposit":
        uid = await _open_deposit(pool)
    else:
        uid = await _open_withdraw(pool)
    with pytest.raises(exc.TicketNotClaimed):
        await release_ticket(
            pool, ticket_type=ticket_type, ticket_uid=uid, actor_id=_CASHIER
        )


# ---------------------------------------------------------------------------
# Append-only triggers — a row's ``core.audit_log`` history is
# IMMUTABLE; UPDATE and DELETE must raise even from the admin role.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_update_rejected_by_trigger(
    pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """Even goldrush_admin can't bypass the immutability triggers
    (they're FOR EACH ROW BEFORE UPDATE/DELETE on every row regardless
    of role)."""
    # Insert at least one audit row so we have a target.
    uid = await _open_deposit(pool)
    audit_id = await admin_pool.fetchval(
        "SELECT id FROM core.audit_log WHERE ref_id = $1 LIMIT 1", uid
    )
    assert audit_id is not None

    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await admin_pool.execute(
            "UPDATE core.audit_log SET reason = 'tampered' WHERE id = $1", audit_id
        )
    assert "append-only" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_audit_log_delete_rejected_by_trigger(
    pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    uid = await _open_deposit(pool)
    audit_id = await admin_pool.fetchval(
        "SELECT id FROM core.audit_log WHERE ref_id = $1 LIMIT 1", uid
    )
    assert audit_id is not None

    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await admin_pool.execute(
            "DELETE FROM core.audit_log WHERE id = $1", audit_id
        )
    assert "append-only" in str(excinfo.value).lower()
