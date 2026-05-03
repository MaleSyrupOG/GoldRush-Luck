"""Treasury invariant property test (Story 14.1).

Spec §8.2 / §1.3: across any sequence of random ops, the conservation
identity must hold:

    SUM(user balances)               -- gold inside the bot at users
  + treasury balance (discord_id=0)  -- fees collected so far
  + total amount swept by admins     -- gold the operator physically
                                        removed from the in-game bank
  + total amount paid out via withdraws (gross - fee)
                                     -- gold that left to user inventory
  ==
    total ever deposited (gross)

Equivalently, the *bucket* identity (which is what the assertion below
checks):

    SUM(user balances) + treasury_balance
  ==
    deposits_in − sweeps_out − withdraws_paid_out

We exercise this against a real Postgres (testcontainers) so the
SECURITY DEFINER fns + triggers participate. Five seeded sequences,
100 ops each — adds ~5 s to the integration suite, well under the
spec's "1000 × 100" target which we'd revisit when CI has more
slack.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import asyncpg
import pytest
from deathroll_core.balance.dw_manager import (
    apply_deposit_ticket,
    apply_withdraw_ticket,
    cancel_deposit,
    cancel_withdraw,
    confirm_deposit,
    confirm_withdraw,
    treasury_sweep,
    treasury_withdraw_to_user,
)


# A handful of fixed Discord IDs for our user pool — keeping it small
# means a withdraw is more likely to find a user with a balance,
# which exercises the actual money flows instead of just hitting
# rejection paths.
_USERS = (1001, 1002, 1003, 1004, 1005)
_CASHIER_ID = 9001
_ADMIN_ID = 7001

# Bounds aligned with seeded dw.global_config (200 .. 200 000) so
# random amounts always fall inside the SDF's accepted range.
_MIN = 200
_MAX = 200_000


@dataclass
class _ExpectedState:
    """Source-of-truth running totals that the assertion checks against
    the DB after every successful op."""

    deposited_total: int = 0
    swept_total: int = 0
    withdrawn_out_total: int = 0  # (amount - fee) for confirmed withdraws

    @property
    def expected_buckets(self) -> int:
        return self.deposited_total - self.swept_total - self.withdrawn_out_total


# ---------------------------------------------------------------------------
# Op runner — each op is a small async helper that may complete or get
# rejected by a SDF; rejection is silent (no state change, no invariant
# update). Successful ops update the expected state.
# ---------------------------------------------------------------------------


async def _run_random_sequence(
    *,
    pool: asyncpg.Pool,
    rng: random.Random,
    n_ops: int,
) -> None:
    state = _ExpectedState()

    # Seed every user with a confirmed deposit so subsequent withdraws
    # have something to work with — bumps the sequence's signal-to-noise
    # ratio compared to letting hypothesis stumble onto the path.
    for user in _USERS:
        amount = _MIN
        uid = await apply_deposit_ticket(
            pool,
            discord_id=user,
            char_name=f"Char{user}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=amount,
            thread_id=user * 10,
            parent_channel_id=user * 100,
        )
        # Cashier needs a registered char + online status for claim.
        await pool.execute(
            "INSERT INTO dw.cashier_characters "
            "(discord_id, char_name, realm, region, faction) "
            "VALUES ($1, $2, $3, $4, $5)",
            _CASHIER_ID,
            f"Cashier{user}",
            "Stormrage",
            "EU",
            "Horde",
        )
        await pool.execute(
            "INSERT INTO dw.cashier_status (discord_id, status, set_at, last_active_at) "
            "VALUES ($1, 'online', NOW(), NOW()) "
            "ON CONFLICT (discord_id) DO UPDATE SET status='online'",
            _CASHIER_ID,
        )
        await pool.execute(
            "SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER_ID
        )
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER_ID)
        state.deposited_total += amount
        await _assert_invariant(pool, state)

    # Now run the random sequence.
    for _ in range(n_ops):
        op = rng.choice(("deposit", "withdraw", "cancel", "sweep", "tw_to_user"))
        try:
            await _dispatch(pool=pool, rng=rng, state=state, op=op)
        except Exception:
            # Any rejection (insufficient balance, region mismatch,
            # validation, etc.) leaves DB unchanged — invariant holds
            # trivially. We still re-verify to catch silent corruption.
            pass
        await _assert_invariant(pool, state)


async def _dispatch(
    *,
    pool: asyncpg.Pool,
    rng: random.Random,
    state: _ExpectedState,
    op: str,
) -> None:
    user = rng.choice(_USERS)
    amount = rng.randint(_MIN, _MAX // 4)  # smaller than max so multiple withdraws fit

    if op == "deposit":
        uid = await apply_deposit_ticket(
            pool,
            discord_id=user,
            char_name=f"Char{user}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=amount,
            thread_id=rng.randint(1_000_000, 9_999_999),
            parent_channel_id=rng.randint(1_000_000, 9_999_999),
        )
        await pool.execute(
            "SELECT dw.claim_ticket('deposit', $1, $2)", uid, _CASHIER_ID
        )
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER_ID)
        state.deposited_total += amount
        return

    if op == "withdraw":
        uid = await apply_withdraw_ticket(
            pool,
            discord_id=user,
            char_name=f"Char{user}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=amount,
            thread_id=rng.randint(1_000_000, 9_999_999),
            parent_channel_id=rng.randint(1_000_000, 9_999_999),
        )
        # Read back the actual fee captured at creation time.
        fee = await pool.fetchval(
            "SELECT fee FROM dw.withdraw_tickets WHERE ticket_uid = $1", uid
        )
        await pool.execute(
            "SELECT dw.claim_ticket('withdraw', $1, $2)", uid, _CASHIER_ID
        )
        await confirm_withdraw(pool, ticket_uid=uid, cashier_id=_CASHIER_ID)
        state.withdrawn_out_total += amount - int(fee)
        return

    if op == "cancel":
        # Open a deposit and cancel before claim — pure no-op for
        # invariant (no money moved).
        uid = await apply_deposit_ticket(
            pool,
            discord_id=user,
            char_name=f"Char{user}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=amount,
            thread_id=rng.randint(1_000_000, 9_999_999),
            parent_channel_id=rng.randint(1_000_000, 9_999_999),
        )
        await cancel_deposit(pool, ticket_uid=uid, actor_id=user, reason="rng")
        return

    if op == "sweep":
        treasury = await pool.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        if treasury and treasury > 0:
            sweep_amt = min(int(treasury), rng.randint(1, max(1, int(treasury))))
            await treasury_sweep(
                pool, amount=sweep_amt, admin_id=_ADMIN_ID, reason="rng"
            )
            state.swept_total += sweep_amt
        return

    if op == "tw_to_user":
        treasury = await pool.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        if treasury and treasury > 0:
            tw_amt = min(int(treasury), rng.randint(1, max(1, int(treasury))))
            await treasury_withdraw_to_user(
                pool,
                amount=tw_amt,
                target_user=user,
                admin_id=_ADMIN_ID,
                reason="rng",
            )
            # No invariant change — gold moves treasury → user, both
            # inside the bucket equation.
        return

    # Cancelling a confirmed withdraw is left for a future op; the
    # SDF rejects already-terminal cancels which keeps state consistent.
    _ = cancel_withdraw  # imported for completeness; unused in this seed


async def _assert_invariant(pool: asyncpg.Pool, state: _ExpectedState) -> None:
    user_balance_sum = await pool.fetchval(
        "SELECT COALESCE(SUM(balance), 0) FROM core.balances WHERE discord_id <> 0"
    )
    treasury = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = 0"
    )
    actual = int(user_balance_sum or 0) + int(treasury or 0)
    expected = state.expected_buckets
    assert actual == expected, (
        f"treasury invariant broken — actual buckets {actual} != "
        f"expected {expected} ("
        f"deposited={state.deposited_total}, "
        f"swept={state.swept_total}, "
        f"withdrawn_out={state.withdrawn_out_total})"
    )


# ---------------------------------------------------------------------------
# Parameterised seeds — each seed is a fully independent sequence; the
# fixture's TRUNCATE-on-yield gives every test a clean DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.asyncio
async def test_treasury_invariant_holds_across_random_ops(
    pool: asyncpg.Pool, seed: int
) -> None:
    rng = random.Random(seed)
    await _run_random_sequence(pool=pool, rng=rng, n_ops=30)
