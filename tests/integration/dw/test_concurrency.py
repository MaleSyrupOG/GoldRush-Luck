"""Concurrency tests (Story 14.3).

Spec §8.2 / §1.3 ACs:

- 100 parallel /withdraw for one user with insufficient balance —
  exactly the correct number succeed.
- 10 parallel /claim on same ticket — exactly one succeeds.
- ``confirm`` racing ``force-cancel-ticket`` — exactly one wins;
  never both apply.
- 100 parallel deposits from 100 distinct new users — exactly 100
  ``core.users`` rows after.

Each test uses ``asyncio.gather`` over the SDF wrappers; the SDFs
themselves take row-level locks (``FOR UPDATE``) which serialise
the contended writes. asyncpg's pool size bounds the actual
parallelism but enough connections fly to expose any missing lock
or non-atomic update.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    apply_deposit_ticket,
    apply_withdraw_ticket,
    cancel_deposit,
    claim_ticket,
    confirm_deposit,
)


_USER = 22222
_CASHIER = 9001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_cashier_with_balance(
    pool: asyncpg.Pool, *, deposit_total: int = 100_000
) -> None:
    """Cashier registered + online, user pre-funded for the test scenario."""
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
    if deposit_total > 0:
        uid = await apply_deposit_ticket(
            pool,
            discord_id=_USER,
            char_name="C",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=deposit_total,
            thread_id=1,
            parent_channel_id=2,
        )
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
        )
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)


# ---------------------------------------------------------------------------
# Parallel /withdraw — only enough succeed to drain the balance.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_withdraws_only_succeed_within_balance(
    pool: asyncpg.Pool,
) -> None:
    """User has 10 000g, fires 50 parallel withdraw-1000 requests.
    At most 10 should succeed; the remaining 40 must hit
    insufficient_balance. SUM-of-locked must never exceed balance."""
    await _seed_cashier_with_balance(pool, deposit_total=10_000)

    async def _try_withdraw(seq: int) -> str:
        """Returns 'ok' on success, 'insufficient' on rejection."""
        try:
            await apply_withdraw_ticket(
                pool,
                discord_id=_USER,
                char_name="C",
                realm="Stormrage",
                region="EU",
                faction="Horde",
                amount=1_000,
                thread_id=10_000 + seq,
                parent_channel_id=20_000 + seq,
            )
            return "ok"
        except exc.InsufficientBalance:
            return "insufficient"
        except exc.AmountOutOfRange:
            return "out_of_range"

    results = await asyncio.gather(*[_try_withdraw(i) for i in range(50)])
    successes = sum(1 for r in results if r == "ok")
    rejections = sum(1 for r in results if r == "insufficient")

    assert successes == 10, f"expected exactly 10 successes, got {successes}"
    assert rejections == 50 - successes, (
        f"non-success non-insufficient outcomes: {results.count('out_of_range')}"
    )

    # User balance should be exactly zero (all locked).
    bal = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = $1", _USER
    )
    assert bal == 0


# ---------------------------------------------------------------------------
# Parallel /claim on the same ticket — exactly one wins.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_claims_on_same_ticket_exactly_one_wins(
    pool: asyncpg.Pool,
) -> None:
    """Register two extra cashiers (all EU/Horde compatible) and have
    10 parallel claims race the same ticket. The SDF takes a row
    lock; only one transaction sees status='open' and wins."""
    await _seed_cashier_with_balance(pool, deposit_total=0)
    extra_cashiers = [9002, 9003, 9004, 9005]
    for cashier_id in extra_cashiers:
        await pool.execute(
            "INSERT INTO dw.cashier_characters "
            "(discord_id, char_name, realm, region, faction) "
            "VALUES ($1, $2, 'Stormrage', 'EU', 'Horde')",
            cashier_id,
            f"Cashier{cashier_id}",
        )
        await pool.execute(
            "INSERT INTO dw.cashier_status "
            "(discord_id, status, set_at, last_active_at) "
            "VALUES ($1, 'online', NOW(), NOW())",
            cashier_id,
        )

    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=999,
        parent_channel_id=998,
    )

    async def _try_claim(cashier_id: int) -> str:
        try:
            await claim_ticket(
                pool, ticket_type="deposit", ticket_uid=uid, cashier_id=cashier_id
            )
            return "ok"
        except exc.TicketAlreadyClaimed:
            return "already_claimed"
        except Exception as e:
            return f"other:{type(e).__name__}"

    contestants = [_CASHIER, *extra_cashiers] * 2  # 10 parallel attempts
    results = await asyncio.gather(*[_try_claim(c) for c in contestants])

    wins = [r for r in results if r == "ok"]
    losses = [r for r in results if r == "already_claimed"]
    assert len(wins) == 1, f"expected 1 winner, got {len(wins)}: {results}"
    assert len(losses) == len(contestants) - 1


# ---------------------------------------------------------------------------
# confirm racing force-cancel — exactly one wins.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_races_force_cancel_exactly_one_wins(
    pool: asyncpg.Pool,
) -> None:
    """Open + claim a ticket, then fire ``confirm`` and ``cancel`` in
    parallel. The row lock serialises them; whichever transitions
    the ticket to a terminal state wins, and the user's balance
    reflects exactly that outcome (never both).

    confirm_deposit is intentionally idempotent on retry by the
    same cashier (returns the current balance without re-crediting).
    So the assertion is on the FINAL STATE + balance, not on the
    count of "ok" responses — multiple confirm calls coexisting
    with at most one cancel is fine; only one transition occurred.
    """
    await _seed_cashier_with_balance(pool, deposit_total=0)
    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=5_000,
        thread_id=300,
        parent_channel_id=301,
    )
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
    )

    async def _confirm() -> str:
        try:
            await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)
            return "confirm_ok"
        except exc.BalanceError as e:
            return f"confirm_err:{type(e).__name__}"

    async def _force_cancel() -> str:
        try:
            await cancel_deposit(pool, ticket_uid=uid, actor_id=0, reason="admin force")
            return "cancel_ok"
        except exc.BalanceError as e:
            return f"cancel_err:{type(e).__name__}"

    # Five of each in parallel for high contention.
    tasks = [_confirm() for _ in range(5)] + [_force_cancel() for _ in range(5)]
    results = await asyncio.gather(*tasks)

    final = await pool.fetchval(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = $1", uid
    )
    bal = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = $1", _USER
    )
    confirm_oks = [r for r in results if r == "confirm_ok"]
    cancel_oks = [r for r in results if r == "cancel_ok"]

    # CRITICAL invariant: the row sits in exactly one terminal state.
    assert final in {"confirmed", "cancelled"}, f"unexpected final {final}"

    # And the balance reflects ONLY ONE transition:
    if final == "confirmed":
        assert bal == 5_000, f"confirmed but balance != 5000 (got {bal})"
        # cancel_ok must not have happened — would have meant the
        # cancel SDF accepted on the confirmed row, breaking the
        # ticket_already_terminal check.
        assert not cancel_oks, (
            f"confirmed but a cancel_ok also returned: {results}"
        )
    else:  # cancelled
        assert bal == 0, f"cancelled but balance != 0 (got {bal})"
        assert not confirm_oks, (
            f"cancelled but a confirm_ok also returned: {results}"
        )


# ---------------------------------------------------------------------------
# 100 parallel deposits from distinct users — 100 core.users rows after.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_deposits_create_distinct_user_rows(pool: asyncpg.Pool) -> None:
    """Each confirm idempotently INSERTs the user row; under heavy
    parallelism with no shared locks the test verifies we don't
    duplicate or lose rows."""
    await _seed_cashier_with_balance(pool, deposit_total=0)

    async def _full_deposit_flow(user_id: int) -> None:
        uid = await apply_deposit_ticket(
            pool,
            discord_id=user_id,
            char_name=f"C{user_id}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=1_000,
            thread_id=user_id * 10,
            parent_channel_id=user_id * 100,
        )
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
        )
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)

    # 100 distinct users, each running the full deposit→claim→confirm
    # path concurrently. The same cashier claims all of them — claim
    # locks per ticket, not per cashier, so this is fine.
    user_ids = list(range(50_000, 50_100))
    await asyncio.gather(*[_full_deposit_flow(u) for u in user_ids])

    # Exactly 100 user rows landed (plus the treasury seed row id=0
    # and the _USER from the cashier seed step which inserted no
    # deposit so doesn't have a users row → so total = 100 + 1).
    n_users = await pool.fetchval(
        "SELECT COUNT(*) FROM core.users WHERE discord_id IN ($1)::BIGINT[] OR discord_id = ANY($2)",
        user_ids,  # placeholder
        user_ids,
    ) if False else await pool.fetchval(
        "SELECT COUNT(*) FROM core.users WHERE discord_id = ANY($1)",
        user_ids,
    )
    assert n_users == 100

    # Each has the deposit credited.
    n_with_balance = await pool.fetchval(
        "SELECT COUNT(*) FROM core.balances "
        "WHERE discord_id = ANY($1) AND balance = 1000",
        user_ids,
    )
    assert n_with_balance == 100
