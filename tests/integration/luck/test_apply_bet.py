"""Story 2.8a — luck.apply_bet SECURITY DEFINER fn.

Spec ref: Luck design §3.4 (stored procedures — economic boundary).

The contract:

- Locks ``core.balances`` FOR UPDATE on the user's row.
- Validates: user exists, not banned, has balance, game enabled,
  game's bet falls in [min_bet, max_bet].
- Computes: ``commission = bet * extra_config.commission_bps / 10000``,
  ``rake = bet * global_config.raffle_rake_bps / 10000``,
  ``effective_stake = bet - commission - rake``.
- Moves gold:
    - user.balance         -= bet_amount
    - user.locked_balance  += effective_stake
    - user.total_wagered   += bet_amount
    - treasury.balance     += commission   (discord_id = 0)
    - active raffle_period.pool_amount += rake
- Inserts ``luck.bets`` with ``status='open'``.
- Emits an audit row via ``core.audit_log_insert_with_chain``.
- Idempotent on ``(discord_id, idempotency_key)``: same key returns
  the existing bet_id without side effects.
- Raises:
    - ``user_not_registered`` if the user has no balance row.
    - ``user_banned`` if the user is in ``banned`` state.
    - ``unknown_game`` if game_name has no game_config row.
    - ``game_paused`` if game_config.enabled is false.
    - ``bet_out_of_range`` if amount < min_bet or > max_bet.
    - ``insufficient_balance`` if user.balance < bet_amount.

Conservation invariant pinned by ``test_conservation_invariant``:

    delta(user_balance + user_locked_balance + treasury + raffle_pool) == 0

across an apply_bet. (Gold is redistributed, not minted/destroyed.)

Permission boundary: only ``deathroll_luck`` has EXECUTE; the
``deathroll_dw`` and ``deathroll_readonly`` roles do not.
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
    """Create core.users + core.balances for a test user with a starting
    balance. Treasury (discord_id=0) is already seeded by the conftest."""
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


async def _call_apply_bet(
    pool: asyncpg.Pool,
    *,
    discord_id: int,
    game_name: str = "coinflip",
    channel_id: int = 0,
    bet_amount: int,
    selection: str = "{}",
    server_seed_hash: bytes = b"\x00" * 32,
    client_seed: str = "cs",
    nonce: int = 0,
    idempotency_key: str = "k1",
    bet_uid: str | None = None,
) -> tuple[int, bool]:
    """Invoke ``luck.apply_bet`` with kwargs; return (bet_id, idempotent)."""
    if bet_uid is None:
        bet_uid = f"BET-{discord_id}-{nonce}-{idempotency_key}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM luck.apply_bet(
              p_discord_id      := $1,
              p_game_name       := $2,
              p_channel_id      := $3,
              p_bet_amount      := $4,
              p_selection       := $5::jsonb,
              p_server_seed_hash := $6,
              p_client_seed     := $7,
              p_nonce           := $8,
              p_idempotency_key := $9,
              p_bet_uid         := $10
            )
            """,
            discord_id,
            game_name,
            channel_id,
            bet_amount,
            selection,
            server_seed_hash,
            client_seed,
            nonce,
            idempotency_key,
            bet_uid,
        )
    return row["bet_id"], row["idempotent"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_apply_bet_happy_path_parametric_game(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A parametric game (coinflip) bet with empty extra_config: no
    commission taken upfront, only the 1 % raffle rake."""
    await _seed_user_with_balance(admin_pool, user_id=1001, balance=10_000)
    await _seed_active_raffle(admin_pool)

    bet_id, idempotent = await _call_apply_bet(
        luck_pool, discord_id=1001, bet_amount=1_000
    )
    assert bet_id > 0
    assert idempotent is False

    # Verify the bet row.
    async with luck_pool.acquire() as conn:
        bet = await conn.fetchrow(
            "SELECT bet_amount, status, game_name FROM luck.bets WHERE id = $1",
            bet_id,
        )
        user = await conn.fetchrow(
            "SELECT balance, locked_balance, total_wagered "
            "FROM core.balances WHERE discord_id = 1001"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        pool_amount = await conn.fetchval(
            "SELECT pool_amount FROM luck.raffle_periods WHERE status = 'active'"
        )

    assert bet["bet_amount"] == 1_000
    assert bet["status"] == "open"
    assert bet["game_name"] == "coinflip"

    # Parametric game: commission_bps=0, rake_bps=100 (1%).
    # commission = 0; rake = 10; effective_stake = 990.
    assert user["balance"] == 9_000  # 10_000 - 1_000
    assert user["locked_balance"] == 990  # effective_stake
    assert user["total_wagered"] == 1_000
    assert treasury == 0  # no commission for parametric
    assert pool_amount == 10  # 1 % rake


async def test_apply_bet_happy_path_blackjack_with_commission(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Blackjack carries a 4.5 % commission_bps in extra_config; that
    fraction goes to treasury at apply_bet time."""
    await _seed_user_with_balance(admin_pool, user_id=1002, balance=10_000)
    await _seed_active_raffle(admin_pool)

    bet_id, _ = await _call_apply_bet(
        luck_pool, discord_id=1002, game_name="blackjack", bet_amount=1_000
    )

    async with luck_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT balance, locked_balance "
            "FROM core.balances WHERE discord_id = 1002"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        pool_amount = await conn.fetchval(
            "SELECT pool_amount FROM luck.raffle_periods WHERE status = 'active'"
        )

    # commission = 1000 * 450 / 10000 = 45
    # rake       = 1000 * 100 / 10000 = 10
    # effective_stake = 1000 - 45 - 10 = 945
    assert user["balance"] == 9_000
    assert user["locked_balance"] == 945
    assert treasury == 45
    assert pool_amount == 10
    _ = bet_id  # bet_id used implicitly via the row inserted


async def test_apply_bet_no_active_raffle_period(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """If no raffle period is active, the rake is captured to treasury
    instead of the pool (so gold is never lost). (We choose 'capture
    to treasury as fallback' over 'reject' so a temporarily-paused
    raffle doesn't block bets.)"""
    await _seed_user_with_balance(admin_pool, user_id=1003, balance=10_000)
    # NO active raffle period seeded.

    await _call_apply_bet(luck_pool, discord_id=1003, bet_amount=1_000)

    async with luck_pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT balance, locked_balance "
            "FROM core.balances WHERE discord_id = 1003"
        )
        treasury = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )

    # rake (10) goes to treasury since there's no active period.
    assert user["balance"] == 9_000
    assert user["locked_balance"] == 990
    assert treasury == 10  # rake fallback to treasury


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_apply_bet_idempotent_same_key_returns_same_id(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Re-running apply_bet with the same (discord_id, idempotency_key)
    returns the same bet_id and does NOT debit the user a second time."""
    await _seed_user_with_balance(admin_pool, user_id=1010, balance=10_000)
    await _seed_active_raffle(admin_pool)

    bet1_id, idempotent1 = await _call_apply_bet(
        luck_pool, discord_id=1010, bet_amount=500, idempotency_key="ix-7"
    )
    assert idempotent1 is False

    bet2_id, idempotent2 = await _call_apply_bet(
        luck_pool, discord_id=1010, bet_amount=500, idempotency_key="ix-7"
    )
    assert bet2_id == bet1_id
    assert idempotent2 is True

    async with luck_pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 1010"
        )
        bet_count = await conn.fetchval(
            "SELECT COUNT(*) FROM luck.bets WHERE discord_id = 1010"
        )
    # Only debited once.
    assert balance == 9_500
    assert bet_count == 1


# ---------------------------------------------------------------------------
# Error paths — every RAISE EXCEPTION path
# ---------------------------------------------------------------------------


async def test_apply_bet_user_not_registered(luck_pool: asyncpg.Pool) -> None:
    """A user with no balance row is rejected (no auto-create)."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="user_not_registered"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 999999, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-X')"
            )


async def test_apply_bet_user_banned(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A banned user cannot place bets."""
    await _seed_user_with_balance(admin_pool, user_id=1020, balance=10_000)
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "UPDATE core.users SET banned = TRUE WHERE discord_id = 1020"
        )
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="user_banned"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1020, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-Y')"
            )


async def test_apply_bet_unknown_game(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """An unknown game_name is rejected before any side effects."""
    await _seed_user_with_balance(admin_pool, user_id=1030, balance=10_000)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="unknown_game"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1030, p_game_name := 'doesnotexist', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-Z')"
            )


async def test_apply_bet_game_paused(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A game with enabled=FALSE is rejected with 'game_paused'."""
    await _seed_user_with_balance(admin_pool, user_id=1040, balance=10_000)
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "UPDATE luck.game_config SET enabled = FALSE WHERE game_name = 'dice'"
        )
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="game_paused"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1040, p_game_name := 'dice', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-P')"
            )


async def test_apply_bet_below_min(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Bet below min_bet is rejected."""
    await _seed_user_with_balance(admin_pool, user_id=1050, balance=10_000)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_out_of_range"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1050, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 50, "  # min=100
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-LO')"
            )


async def test_apply_bet_above_max(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Bet above max_bet is rejected."""
    await _seed_user_with_balance(admin_pool, user_id=1051, balance=10_000_000)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="bet_out_of_range"):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1051, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 600000, "  # max=500000
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-HI')"
            )


async def test_apply_bet_insufficient_balance(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Bet > balance is rejected; no side effects."""
    await _seed_user_with_balance(admin_pool, user_id=1060, balance=200)
    async with luck_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.RaiseError, match="insufficient_balance"
        ):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 1060, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 1000, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-NF')"
            )

    # No side effects: balance unchanged, no bet row.
    async with luck_pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 1060"
        )
        bet_count = await conn.fetchval(
            "SELECT COUNT(*) FROM luck.bets WHERE discord_id = 1060"
        )
    assert balance == 200
    assert bet_count == 0


# ---------------------------------------------------------------------------
# Conservation invariant
# ---------------------------------------------------------------------------


async def test_apply_bet_conservation(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Across an apply_bet, the total gold in the system is conserved:

    delta(user.balance + user.locked_balance + treasury + raffle_pool) == 0
    """
    await _seed_user_with_balance(admin_pool, user_id=2001, balance=50_000)
    await _seed_active_raffle(admin_pool, pool=100)

    async with admin_pool.acquire() as conn:
        before = await conn.fetchrow(
            """
            SELECT
              (SELECT balance + locked_balance FROM core.balances WHERE discord_id = 2001) AS user_total,
              (SELECT balance FROM core.balances WHERE discord_id = 0) AS treasury,
              (SELECT pool_amount FROM luck.raffle_periods WHERE status='active') AS pool
            """
        )
    await _call_apply_bet(
        luck_pool, discord_id=2001, game_name="blackjack", bet_amount=10_000
    )
    async with admin_pool.acquire() as conn:
        after = await conn.fetchrow(
            """
            SELECT
              (SELECT balance + locked_balance FROM core.balances WHERE discord_id = 2001) AS user_total,
              (SELECT balance FROM core.balances WHERE discord_id = 0) AS treasury,
              (SELECT pool_amount FROM luck.raffle_periods WHERE status='active') AS pool
            """
        )

    sum_before = before["user_total"] + before["treasury"] + before["pool"]
    sum_after = after["user_total"] + after["treasury"] + after["pool"]
    assert sum_before == sum_after, (
        f"conservation violated: before={sum_before}, after={sum_after}"
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_apply_bet_writes_audit_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """An audit row is written with action='bet_placed' + bet metadata."""
    await _seed_user_with_balance(admin_pool, user_id=3001, balance=10_000)
    await _seed_active_raffle(admin_pool)

    bet_id, _ = await _call_apply_bet(
        luck_pool, discord_id=3001, bet_amount=1_000
    )

    async with admin_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT actor_id, action, ref_type, ref_id, metadata
            FROM core.audit_log
            WHERE actor_id = 3001 AND action = 'bet_placed'
            ORDER BY id ASC
            """
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["actor_id"] == 3001
    assert row["action"] == "bet_placed"
    assert row["ref_type"] == "luck_bet"
    assert row["ref_id"] == str(bet_id)


# ---------------------------------------------------------------------------
# Permission boundary
# ---------------------------------------------------------------------------


async def test_apply_bet_readonly_role_no_execute(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_readonly does NOT have EXECUTE on luck.apply_bet."""
    await _seed_user_with_balance(admin_pool, user_id=4001, balance=10_000)
    async with readonly_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.InsufficientPrivilegeError
        ):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 4001, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-RO')"
            )


async def test_apply_bet_dw_role_no_execute(
    dw_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_dw does NOT have EXECUTE on luck.apply_bet (D/W is the
    economic frontier; it shouldn't be calling Luck game settlement
    functions)."""
    await _seed_user_with_balance(admin_pool, user_id=4002, balance=10_000)
    async with dw_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.InsufficientPrivilegeError
        ):
            await conn.execute(
                "SELECT luck.apply_bet("
                "  p_discord_id := 4002, p_game_name := 'coinflip', "
                "  p_channel_id := 0, p_bet_amount := 100, "
                "  p_selection := '{}'::jsonb, "
                "  p_server_seed_hash := '\\x00'::bytea, "
                "  p_client_seed := 'cs', p_nonce := 0, "
                "  p_idempotency_key := 'k', p_bet_uid := 'BET-DW')"
            )
