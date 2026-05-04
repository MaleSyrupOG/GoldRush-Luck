"""Story 2.8b — luck.resolve_bet + luck.refund_bet + luck.cashout_mines.

Spec ref: Luck design §3.4.

These three SDFs close out an ``open`` bet:

- ``luck.resolve_bet(p_bet_id, p_status, p_payout, p_outcome)``
  resolves a bet with a known terminal status. Statuses:
    - ``resolved_win``  : balance += payout; locked_balance -=
                         effective_stake; treasury -= (payout -
                         effective_stake) (could be ±).
    - ``resolved_loss`` : balance += 0; locked_balance -= effective_stake;
                         treasury += effective_stake (the lost stake
                         goes to the house).
    - ``resolved_tie``  : balance += effective_stake (return playable
                         portion); locked_balance -= effective_stake;
                         treasury delta = 0.
                         (A tie does NOT refund commission + rake —
                         the house keeps its cut. v1 design decision.)

- ``luck.refund_bet(p_bet_id, p_reason)`` is for void/error cases:
  full unwind of apply_bet. balance += bet_amount; locked_balance -=
  effective_stake; treasury -= commission; pool -= rake (or treasury
  -= rake if the bet's rake went to treasury). Status -> 'refunded'.

- ``luck.cashout_mines(p_bet_id, p_multiplier)`` is the multi-round
  cashout for Mines: payout = effective_stake * multiplier. Resolves
  with status='resolved_win'.

All three are idempotent: re-running on a non-open bet either
returns silently (if state matches) or raises 'bet_not_open'.

Conservation invariant pinned across every flow:

    delta(SUM(core.balances) + SUM(luck.raffle_periods.pool_amount)) == 0

Permission boundary: deathroll_luck has EXECUTE; deathroll_dw and
deathroll_readonly do not.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_with_balance(
    admin_pool: asyncpg.Pool, *, user_id: int, balance: int
) -> None:
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )
        await conn.execute(
            "INSERT INTO core.balances (discord_id, balance) "
            "VALUES ($1, $2) ON CONFLICT (discord_id) "
            "DO UPDATE SET balance = $2",
            user_id,
            balance,
        )


async def _seed_active_raffle(
    admin_pool: asyncpg.Pool, *, period_id: int = 1, pool: int = 0
) -> None:
    async with admin_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status, pool_amount)
            VALUES ($1, $2,
                    '2026-05-01T00:00Z',
                    '2026-05-31T23:59Z',
                    'active', $3)
            ON CONFLICT (id) DO UPDATE SET
              status = 'active', pool_amount = $3
            """,
            period_id,
            f"test-period-{period_id}",
            pool,
        )


async def _open_bet(
    pool: asyncpg.Pool,
    *,
    discord_id: int,
    game_name: str = "coinflip",
    bet_amount: int,
    idempotency_key: str = "k1",
    bet_uid: str | None = None,
) -> int:
    """Open a bet via apply_bet; return the bet_id."""
    if bet_uid is None:
        bet_uid = f"BET-{discord_id}-{idempotency_key}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM luck.apply_bet(
              p_discord_id      := $1,
              p_game_name       := $2,
              p_channel_id      := 0,
              p_bet_amount      := $3,
              p_selection       := '{}'::jsonb,
              p_server_seed_hash := '\\x00'::bytea,
              p_client_seed     := 'cs',
              p_nonce           := 0,
              p_idempotency_key := $4,
              p_bet_uid         := $5
            )
            """,
            discord_id,
            game_name,
            bet_amount,
            idempotency_key,
            bet_uid,
        )
    return row["bet_id"]


async def _system_total(admin_pool: asyncpg.Pool) -> int:
    """Conservation total: sum of all balances + raffle pools."""
    async with admin_pool.acquire() as conn:
        balances = await conn.fetchval(
            "SELECT COALESCE(SUM(balance + locked_balance), 0) FROM core.balances"
        )
        pool = await conn.fetchval(
            "SELECT COALESCE(SUM(pool_amount), 0) FROM luck.raffle_periods"
        )
    return int(balances) + int(pool)


# ---------------------------------------------------------------------------
# apply_bet now persists effective_stake / commission / rake / rake_period_id
# ---------------------------------------------------------------------------


async def test_apply_bet_persists_resolution_columns(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The four new bet-resolution columns are populated by apply_bet."""
    await _seed_user_with_balance(admin_pool, user_id=5001, balance=10_000)
    await _seed_active_raffle(admin_pool, period_id=1)

    bet_id = await _open_bet(
        luck_pool, discord_id=5001, game_name="blackjack", bet_amount=1_000
    )
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT effective_stake, commission, rake, rake_period_id "
            "FROM luck.bets WHERE id = $1",
            bet_id,
        )
    # blackjack: commission_bps=450 → 45; rake=10; effective_stake=945.
    assert row["effective_stake"] == 945
    assert row["commission"] == 45
    assert row["rake"] == 10
    assert row["rake_period_id"] == 1


async def test_apply_bet_rake_period_null_when_no_active(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """When no raffle period is active, rake_period_id is NULL (rake
    fell to treasury)."""
    await _seed_user_with_balance(admin_pool, user_id=5002, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5002, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rake_period_id FROM luck.bets WHERE id = $1", bet_id
        )
    assert row["rake_period_id"] is None


# ---------------------------------------------------------------------------
# resolve_bet — happy paths
# ---------------------------------------------------------------------------


async def test_resolve_bet_win_2x(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A 2x win on a parametric coinflip: payout = 2 * effective_stake.

    bet=1000, commission=0, rake=10, effective_stake=990
    payout = 1980 (2x effective_stake). Conservation:
      treasury -= (payout - effective_stake) = -990
    """
    await _seed_user_with_balance(admin_pool, user_id=5101, balance=10_000)
    await _seed_active_raffle(admin_pool, pool=10_000)
    # Pre-fund the treasury so it can pay the win without going negative.
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "UPDATE core.balances SET balance = 100_000 WHERE discord_id = 0"
        )

    before = await _system_total(admin_pool)
    bet_id = await _open_bet(luck_pool, discord_id=5101, bet_amount=1_000)

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_win', "
            "  p_payout := 1980, p_outcome := '{}'::jsonb)",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        bet = await conn.fetchrow(
            "SELECT status, payout_amount, profit, resolved_at, outcome "
            "FROM luck.bets WHERE id = $1",
            bet_id,
        )
        user = await conn.fetchrow(
            "SELECT balance, locked_balance, total_won "
            "FROM core.balances WHERE discord_id = 5101"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )

    assert bet["status"] == "resolved_win"
    assert bet["payout_amount"] == 1980
    assert bet["profit"] == 980  # 1980 - 1000
    assert bet["resolved_at"] is not None
    assert bet["outcome"] is not None

    # User: started 10_000; -1000 at apply (balance=9000); +1980 at resolve
    # (balance=10_980); locked released (locked_balance=0); total_won=1980.
    assert user["balance"] == 10_980
    assert user["locked_balance"] == 0
    assert user["total_won"] == 1980

    # Treasury: started 100_000; got rake-no-active=0 (raffle was active,
    # so rake went to pool); paid out (1980 - 990) = 990 in extra.
    # Net treasury: 100_000 - 990 = 99_010.
    assert treasury == 99_010

    after = await _system_total(admin_pool)
    assert after == before, f"conservation violated: before={before} after={after}"


async def test_resolve_bet_loss(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A loss: payout=0; locked_balance released to nothing; treasury
    gains the lost effective_stake."""
    await _seed_user_with_balance(admin_pool, user_id=5102, balance=10_000)
    await _seed_active_raffle(admin_pool)

    before = await _system_total(admin_pool)
    bet_id = await _open_bet(luck_pool, discord_id=5102, bet_amount=1_000)

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT balance, locked_balance, total_won "
            "FROM core.balances WHERE discord_id = 5102"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )

    # User: -1000 from apply, +0 from resolve. balance=9000.
    # locked released (was 990).
    assert user["balance"] == 9_000
    assert user["locked_balance"] == 0
    assert user["total_won"] == 0

    # Treasury: gained the effective_stake (990) at resolve.
    assert treasury == 990

    after = await _system_total(admin_pool)
    assert after == before


async def test_resolve_bet_tie(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A tie: returns effective_stake to balance; commission + rake
    NOT refunded (house keeps its cut)."""
    await _seed_user_with_balance(admin_pool, user_id=5103, balance=10_000)
    await _seed_active_raffle(admin_pool)

    before = await _system_total(admin_pool)
    bet_id = await _open_bet(
        luck_pool, discord_id=5103, game_name="blackjack", bet_amount=1_000
    )
    # blackjack: commission=45, rake=10, effective_stake=945

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_tie', "
            "  p_payout := 945, p_outcome := '{}'::jsonb)",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT balance, locked_balance "
            "FROM core.balances WHERE discord_id = 5103"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        pool_amount = await conn.fetchval(
            "SELECT pool_amount FROM luck.raffle_periods WHERE id = 1"
        )

    # User: -1000 from apply, +945 from tie. balance = 9_945.
    # locked released.
    assert user["balance"] == 9_945
    assert user["locked_balance"] == 0
    # Treasury kept its commission. Pool kept its rake.
    assert treasury == 45  # commission
    assert pool_amount == 10  # rake

    after = await _system_total(admin_pool)
    assert after == before


# ---------------------------------------------------------------------------
# resolve_bet — error paths
# ---------------------------------------------------------------------------


async def test_resolve_bet_unknown_id_raises(luck_pool: asyncpg.Pool) -> None:
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_not_found"):
            await conn.execute(
                "SELECT luck.resolve_bet("
                "  p_bet_id := 999999, p_status := 'resolved_win', "
                "  p_payout := 0, p_outcome := '{}'::jsonb)"
            )


async def test_resolve_bet_already_resolved_is_idempotent(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Re-running resolve on a resolved bet with the same status is a
    no-op (idempotent)."""
    await _seed_user_with_balance(admin_pool, user_id=5104, balance=10_000)
    await _seed_active_raffle(admin_pool)
    bet_id = await _open_bet(luck_pool, discord_id=5104, bet_amount=1_000)

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )
        # Second call: same status, should NOT double-debit.
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT balance, locked_balance "
            "FROM core.balances WHERE discord_id = 5104"
        )
    # Only one debit applied.
    assert user["balance"] == 9_000
    assert user["locked_balance"] == 0


async def test_resolve_bet_changing_terminal_status_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Once resolved, can't be flipped to a different terminal status."""
    await _seed_user_with_balance(admin_pool, user_id=5105, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5105, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_already_terminal"):
            await conn.execute(
                "SELECT luck.resolve_bet("
                "  p_bet_id := $1, p_status := 'resolved_win', "
                "  p_payout := 1980, p_outcome := '{}'::jsonb)",
                bet_id,
            )


async def test_resolve_bet_invalid_status_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5106, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5106, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="invalid_status"):
            await conn.execute(
                "SELECT luck.resolve_bet("
                "  p_bet_id := $1, p_status := 'pending_review', "
                "  p_payout := 0, p_outcome := '{}'::jsonb)",
                bet_id,
            )


# ---------------------------------------------------------------------------
# refund_bet
# ---------------------------------------------------------------------------


async def test_refund_bet_full_unwind(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """refund_bet is the full apply_bet unwind: user gets bet_amount
    back, treasury gives back commission, pool gives back rake."""
    await _seed_user_with_balance(admin_pool, user_id=5201, balance=10_000)
    await _seed_active_raffle(admin_pool, period_id=1, pool=0)

    before = await _system_total(admin_pool)
    bet_id = await _open_bet(
        luck_pool, discord_id=5201, game_name="blackjack", bet_amount=1_000
    )

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.refund_bet("
            "  p_bet_id := $1, p_reason := 'admin_void')",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        bet = await conn.fetchrow(
            "SELECT status, payout_amount, profit FROM luck.bets WHERE id = $1",
            bet_id,
        )
        user = await conn.fetchrow(
            "SELECT balance, locked_balance, total_wagered "
            "FROM core.balances WHERE discord_id = 5201"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        pool_amount = await conn.fetchval(
            "SELECT pool_amount FROM luck.raffle_periods WHERE id = 1"
        )

    assert bet["status"] == "refunded"
    assert bet["payout_amount"] == 0
    assert bet["profit"] == 0

    # User: full refund — balance back to 10_000; locked to 0;
    # total_wagered also reverted (since the bet was voided).
    assert user["balance"] == 10_000
    assert user["locked_balance"] == 0
    assert user["total_wagered"] == 0

    # Treasury and pool both unwound.
    assert treasury == 0
    assert pool_amount == 0

    after = await _system_total(admin_pool)
    assert after == before


async def test_refund_bet_with_no_period(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """When the bet's rake fell to treasury (no active period at apply),
    refund returns the rake from treasury rather than from a pool."""
    await _seed_user_with_balance(admin_pool, user_id=5202, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5202, bet_amount=1_000)
    # bet's rake_period_id is NULL; rake (10) went to treasury.

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.refund_bet("
            "  p_bet_id := $1, p_reason := 'admin_void')",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        user_bal = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 5202"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
    assert user_bal == 10_000
    assert treasury == 0


async def test_refund_bet_unknown_id_raises(luck_pool: asyncpg.Pool) -> None:
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_not_found"):
            await conn.execute(
                "SELECT luck.refund_bet("
                "  p_bet_id := 999999, p_reason := 'admin_void')"
            )


async def test_refund_bet_already_refunded_idempotent(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5203, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5203, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.refund_bet(p_bet_id := $1, p_reason := 'r1')",
            bet_id,
        )
        # Second call no-ops.
        await conn.execute(
            "SELECT luck.refund_bet(p_bet_id := $1, p_reason := 'r2')",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        user_bal = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 5203"
        )
    assert user_bal == 10_000


async def test_refund_bet_after_resolve_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Cannot refund a bet that has already been resolved."""
    await _seed_user_with_balance(admin_pool, user_id=5204, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5204, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_already_terminal"):
            await conn.execute(
                "SELECT luck.refund_bet(p_bet_id := $1, p_reason := 'too late')",
                bet_id,
            )


# ---------------------------------------------------------------------------
# cashout_mines
# ---------------------------------------------------------------------------


async def test_cashout_mines_happy_path(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """cashout_mines pays effective_stake * multiplier as a win."""
    await _seed_user_with_balance(admin_pool, user_id=5301, balance=10_000)
    await _seed_active_raffle(admin_pool, pool=10_000)
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "UPDATE core.balances SET balance = 100_000 WHERE discord_id = 0"
        )

    bet_id = await _open_bet(
        luck_pool, discord_id=5301, game_name="mines", bet_amount=1_000
    )
    # mines: commission=0, rake=10, effective_stake=990.

    # Cashout at 1.5x: payout = 990 * 1.5 = 1485.
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.cashout_mines("
            "  p_bet_id := $1, p_multiplier := 1.5)",
            bet_id,
        )

    async with luck_pool.acquire() as conn:
        bet = await conn.fetchrow(
            "SELECT status, payout_amount FROM luck.bets WHERE id = $1",
            bet_id,
        )
        user_bal = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 5301"
        )

    assert bet["status"] == "resolved_win"
    assert bet["payout_amount"] == 1485
    # User: -1000 from apply + 1485 cashout.
    assert user_bal == 10_485


async def test_cashout_mines_wrong_game_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """cashout_mines rejects non-mines bets."""
    await _seed_user_with_balance(admin_pool, user_id=5302, balance=10_000)
    bet_id = await _open_bet(
        luck_pool, discord_id=5302, game_name="coinflip", bet_amount=1_000
    )
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="not_mines_bet"):
            await conn.execute(
                "SELECT luck.cashout_mines("
                "  p_bet_id := $1, p_multiplier := 1.5)",
                bet_id,
            )


async def test_cashout_mines_invalid_multiplier_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Multiplier must be > 0."""
    await _seed_user_with_balance(admin_pool, user_id=5303, balance=10_000)
    bet_id = await _open_bet(
        luck_pool, discord_id=5303, game_name="mines", bet_amount=1_000
    )
    async with luck_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.RaiseError, match="invalid_multiplier"
        ):
            await conn.execute(
                "SELECT luck.cashout_mines("
                "  p_bet_id := $1, p_multiplier := -1.0)",
                bet_id,
            )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_resolve_bet_writes_audit_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5401, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5401, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.resolve_bet("
            "  p_bet_id := $1, p_status := 'resolved_loss', "
            "  p_payout := 0, p_outcome := '{}'::jsonb)",
            bet_id,
        )
    async with admin_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action FROM core.audit_log WHERE actor_id = 5401 "
            "ORDER BY id ASC"
        )
    actions = [r["action"] for r in rows]
    assert "bet_placed" in actions
    assert "bet_resolved" in actions


async def test_refund_bet_writes_audit_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5402, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5402, bet_amount=1_000)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT luck.refund_bet(p_bet_id := $1, p_reason := 'test')",
            bet_id,
        )
    async with admin_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action FROM core.audit_log WHERE actor_id = 5402 "
            "ORDER BY id ASC"
        )
    actions = [r["action"] for r in rows]
    assert "bet_placed" in actions
    assert "bet_refunded" in actions


# ---------------------------------------------------------------------------
# Permission boundary
# ---------------------------------------------------------------------------


async def test_resolve_bet_readonly_no_execute(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool, luck_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5501, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5501, bet_amount=1_000)
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT luck.resolve_bet("
                "  p_bet_id := $1, p_status := 'resolved_loss', "
                "  p_payout := 0, p_outcome := '{}'::jsonb)",
                bet_id,
            )


async def test_refund_bet_readonly_no_execute(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool, luck_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5502, balance=10_000)
    bet_id = await _open_bet(luck_pool, discord_id=5502, bet_amount=1_000)
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT luck.refund_bet(p_bet_id := $1, p_reason := 'r')",
                bet_id,
            )


async def test_cashout_mines_readonly_no_execute(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool, luck_pool: asyncpg.Pool
) -> None:
    await _seed_user_with_balance(admin_pool, user_id=5503, balance=10_000)
    bet_id = await _open_bet(
        luck_pool, discord_id=5503, game_name="mines", bet_amount=1_000
    )
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT luck.cashout_mines("
                "  p_bet_id := $1, p_multiplier := 1.5)",
                bet_id,
            )
