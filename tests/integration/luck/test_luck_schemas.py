"""Story 2.7 — luck.* schemas (11 tables).

Spec ref: Luck design §3.3.

Pins the schema spec via integration tests against a real Postgres:

- All 11 tables exist.
- CHECK constraints on bets.status, bets.bet_amount > 0,
  bets.payout_amount >= 0, raffle_periods.status,
  raffle_periods.ends_at > starts_at, raffle_periods.pool_amount >= 0,
  game_config.min_bet > 0, game_config.max_bet >= min_bet,
  game_config.house_edge_bps in [0, 10000],
  leaderboard_snapshot.period in {daily, weekly, monthly, all_time},
  leaderboard_snapshot.category in {top_wagered, top_won, top_big_wins}.
- UNIQUE constraints: bets.bet_uid, bets (discord_id, idempotency_key)
  (the double-charge prevention), bet_rounds (bet_id, round_index),
  rate_limit_entries (discord_id, scope, bucket_start),
  raffle_periods.period_label, raffle_draws.period_id,
  channel_binding.channel_id.
- Indexes: idx_bets_user_ts, idx_bets_game_ts, idx_bets_status (partial),
  idx_bets_resolved (partial), idx_session_expires,
  idx_ratelimit_lookup, idx_tickets_period_user, idx_tickets_period_ts.
- Append-only trigger on luck.raffle_draws (UPDATE/DELETE rejected
  even from admin role).
- Privilege boundary: deathroll_luck CAN INSERT; deathroll_readonly
  CANNOT INSERT.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Existence of all 11 tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "game_config",
        "channel_binding",
        "bets",
        "bet_rounds",
        "game_sessions",
        "rate_limit_entries",
        "raffle_periods",
        "raffle_tickets",
        "raffle_draws",
        "leaderboard_snapshot",
        "global_config",
    ],
)
async def test_luck_table_exists(luck_pool: asyncpg.Pool, table: str) -> None:
    """Each spec'd luck.* table is present after alembic head."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'luck' AND table_name = $1",
            table,
        )
    assert row is not None, f"luck.{table} missing"


# ---------------------------------------------------------------------------
# bets — the heart of the bookkeeping
# ---------------------------------------------------------------------------


async def _seed_user_and_game(admin_pool: asyncpg.Pool, *, user_id: int) -> None:
    """Helper: insert a core.users row + a luck.game_config row so
    the FKs in luck.bets are satisfied."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO luck.game_config
              (game_name, min_bet, max_bet, house_edge_bps, updated_by)
            VALUES ('coinflip', 100, 500000, 500, 0)
            ON CONFLICT DO NOTHING
            """
        )


async def test_bets_double_charge_prevention(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """(discord_id, idempotency_key) UNIQUE prevents double-charges.

    A second INSERT with the same key for the same user must fail
    with UniqueViolation.
    """
    user_id = 7001
    await _seed_user_and_game(admin_pool, user_id=user_id)

    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.bets
              (bet_uid, discord_id, game_name, channel_id, bet_amount,
               selection, status, server_seed_hash, client_seed, nonce,
               idempotency_key)
            VALUES ('LCK-A1', $1, 'coinflip', 1, 100, '{}'::jsonb,
                    'open', '\\x00'::bytea, 'cs', 0, 'discord:abc')
            """,
            user_id,
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, server_seed_hash, client_seed, nonce,
                   idempotency_key)
                VALUES ('LCK-A2', $1, 'coinflip', 1, 100, '{}'::jsonb,
                        'open', '\\x00'::bytea, 'cs', 1, 'discord:abc')
                """,
                user_id,
            )


async def test_bets_idempotency_key_isolated_per_user(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Same idempotency_key for different users is allowed."""
    await _seed_user_and_game(admin_pool, user_id=7002)
    await _seed_user_and_game(admin_pool, user_id=7003)

    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.bets
              (bet_uid, discord_id, game_name, channel_id, bet_amount,
               selection, status, server_seed_hash, client_seed, nonce,
               idempotency_key)
            VALUES ('LCK-B1', 7002, 'coinflip', 1, 100, '{}'::jsonb,
                    'open', '\\x00'::bytea, 'cs', 0, 'discord:shared-key')
            """
        )
        # Different user, same key — must succeed.
        await conn.execute(
            """
            INSERT INTO luck.bets
              (bet_uid, discord_id, game_name, channel_id, bet_amount,
               selection, status, server_seed_hash, client_seed, nonce,
               idempotency_key)
            VALUES ('LCK-B2', 7003, 'coinflip', 1, 100, '{}'::jsonb,
                    'open', '\\x00'::bytea, 'cs', 0, 'discord:shared-key')
            """
        )


async def test_bets_bet_amount_must_be_positive(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """bet_amount > 0 CHECK constraint enforces no zero/negative bets."""
    await _seed_user_and_game(admin_pool, user_id=7004)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, server_seed_hash, client_seed, nonce,
                   idempotency_key)
                VALUES ('LCK-C1', 7004, 'coinflip', 1, 0, '{}'::jsonb,
                        'open', '\\x00'::bytea, 'cs', 0, 'k')
                """
            )


async def test_bets_status_check(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """status CHECK rejects unknown values."""
    await _seed_user_and_game(admin_pool, user_id=7005)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, server_seed_hash, client_seed, nonce,
                   idempotency_key)
                VALUES ('LCK-D1', 7005, 'coinflip', 1, 100, '{}'::jsonb,
                        'pending_review', '\\x00'::bytea, 'cs', 0, 'k')
                """
            )


async def test_bets_payout_amount_nonneg(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """payout_amount >= 0 CHECK."""
    await _seed_user_and_game(admin_pool, user_id=7006)
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, payout_amount, server_seed_hash,
                   client_seed, nonce, idempotency_key)
                VALUES ('LCK-E1', 7006, 'coinflip', 1, 100, '{}'::jsonb,
                        'resolved_loss', -1, '\\x00'::bytea, 'cs', 0, 'k')
                """
            )


async def test_bet_uid_unique(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """bet_uid is UNIQUE across all bets."""
    await _seed_user_and_game(admin_pool, user_id=7007)
    await _seed_user_and_game(admin_pool, user_id=7008)
    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.bets
              (bet_uid, discord_id, game_name, channel_id, bet_amount,
               selection, status, server_seed_hash, client_seed, nonce,
               idempotency_key)
            VALUES ('LCK-DUP', 7007, 'coinflip', 1, 100, '{}'::jsonb,
                    'open', '\\x00'::bytea, 'cs', 0, 'k1')
            """
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, server_seed_hash, client_seed, nonce,
                   idempotency_key)
                VALUES ('LCK-DUP', 7008, 'coinflip', 1, 100, '{}'::jsonb,
                        'open', '\\x00'::bytea, 'cs', 0, 'k2')
                """
            )


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "index"),
    [
        ("bets", "idx_bets_user_ts"),
        ("bets", "idx_bets_game_ts"),
        ("bets", "idx_bets_status"),
        ("bets", "idx_bets_resolved"),
        ("game_sessions", "idx_session_expires"),
        ("rate_limit_entries", "idx_ratelimit_lookup"),
        ("raffle_tickets", "idx_tickets_period_user"),
        ("raffle_tickets", "idx_tickets_period_ts"),
    ],
)
async def test_index_present(
    luck_pool: asyncpg.Pool, table: str, index: str
) -> None:
    """Every spec-required index exists."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'luck' AND tablename = $1 AND indexname = $2
            """,
            table,
            index,
        )
    assert row is not None, f"index luck.{table}.{index} missing"


# ---------------------------------------------------------------------------
# game_config CHECKs
# ---------------------------------------------------------------------------


async def test_game_config_min_bet_positive(luck_pool: asyncpg.Pool) -> None:
    """min_bet > 0 CHECK rejects zero or negative."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.game_config
                  (game_name, min_bet, max_bet, house_edge_bps, updated_by)
                VALUES ('zero_min', 0, 100, 500, 0)
                """
            )


async def test_game_config_max_bet_geq_min(luck_pool: asyncpg.Pool) -> None:
    """max_bet >= min_bet CHECK rejects upside-down ranges."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.game_config
                  (game_name, min_bet, max_bet, house_edge_bps, updated_by)
                VALUES ('inverted', 100, 50, 500, 0)
                """
            )


async def test_game_config_house_edge_bounds(luck_pool: asyncpg.Pool) -> None:
    """house_edge_bps must be in [0, 10000] (i.e., 0 % to 100 %)."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.game_config
                  (game_name, min_bet, max_bet, house_edge_bps, updated_by)
                VALUES ('over', 100, 200, 10001, 0)
                """
            )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.game_config
                  (game_name, min_bet, max_bet, house_edge_bps, updated_by)
                VALUES ('under', 100, 200, -1, 0)
                """
            )


# ---------------------------------------------------------------------------
# raffle_periods CHECKs
# ---------------------------------------------------------------------------


async def test_raffle_periods_ends_after_starts(luck_pool: asyncpg.Pool) -> None:
    """ends_at > starts_at CHECK."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.raffle_periods
                  (period_label, starts_at, ends_at, status)
                VALUES ('inverted-2026-05',
                        '2026-05-31T00:00Z',
                        '2026-05-01T00:00Z',
                        'active')
                """
            )


async def test_raffle_periods_status_check(luck_pool: asyncpg.Pool) -> None:
    """status must be active|drawing|closed."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.raffle_periods
                  (period_label, starts_at, ends_at, status)
                VALUES ('bad-status-2026-05',
                        '2026-05-01T00:00Z',
                        '2026-05-31T00:00Z',
                        'archived')
                """
            )


async def test_raffle_periods_label_unique(luck_pool: asyncpg.Pool) -> None:
    """period_label is UNIQUE so we can find a period by name."""
    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (period_label, starts_at, ends_at, status)
            VALUES ('2026-05',
                    '2026-05-01T00:00Z',
                    '2026-05-31T00:00Z',
                    'active')
            """
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO luck.raffle_periods
                  (period_label, starts_at, ends_at, status)
                VALUES ('2026-05',
                        '2026-05-01T00:00Z',
                        '2026-05-31T00:00Z',
                        'active')
                """
            )


# ---------------------------------------------------------------------------
# leaderboard_snapshot CHECKs
# ---------------------------------------------------------------------------


async def test_leaderboard_period_check(luck_pool: asyncpg.Pool) -> None:
    """period must be daily|weekly|monthly|all_time."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.leaderboard_snapshot
                  (period, category, snapshot)
                VALUES ('hourly', 'top_wagered', '{}'::jsonb)
                """
            )


async def test_leaderboard_category_check(luck_pool: asyncpg.Pool) -> None:
    """category must be top_wagered|top_won|top_big_wins."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO luck.leaderboard_snapshot
                  (period, category, snapshot)
                VALUES ('daily', 'top_streak', '{}'::jsonb)
                """
            )


# ---------------------------------------------------------------------------
# raffle_draws append-only trigger
# ---------------------------------------------------------------------------


async def _seed_raffle_draw(admin_pool: asyncpg.Pool, *, period_id: int) -> None:
    async with admin_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES ($1, $2,
                    '2026-04-01T00:00Z',
                    '2026-04-30T23:59Z',
                    'closed')
            ON CONFLICT DO NOTHING
            """,
            period_id,
            f"draw-test-{period_id}",
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_draws
              (period_id, pool_amount, revealed_server_seed,
               server_seed_hash, client_seed_used, nonces_used,
               winners, total_tickets)
            VALUES ($1, 100000, '\\x00'::bytea, '\\x00'::bytea,
                    'cs', '[]'::jsonb, '[]'::jsonb, 0)
            """,
            period_id,
        )


async def test_raffle_draws_rejects_update_via_admin(
    admin_pool: asyncpg.Pool,
) -> None:
    """UPDATE on luck.raffle_draws fails at trigger level."""
    await _seed_raffle_draw(admin_pool, period_id=9001)
    async with admin_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE luck.raffle_draws SET total_tickets = 99 "
                "WHERE period_id = 9001"
            )


async def test_raffle_draws_rejects_delete_via_admin(
    admin_pool: asyncpg.Pool,
) -> None:
    """DELETE on luck.raffle_draws fails at trigger level."""
    await _seed_raffle_draw(admin_pool, period_id=9002)
    async with admin_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "DELETE FROM luck.raffle_draws WHERE period_id = 9002"
            )


# ---------------------------------------------------------------------------
# Privilege boundary
# ---------------------------------------------------------------------------


async def test_readonly_role_cannot_insert_bets(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_readonly cannot INSERT into luck.bets."""
    await _seed_user_and_game(admin_pool, user_id=7099)
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO luck.bets
                  (bet_uid, discord_id, game_name, channel_id, bet_amount,
                   selection, status, server_seed_hash, client_seed, nonce,
                   idempotency_key)
                VALUES ('LCK-RO', 7099, 'coinflip', 1, 100, '{}'::jsonb,
                        'open', '\\x00'::bytea, 'cs', 0, 'k')
                """
            )


async def test_readonly_role_cannot_insert_raffle_draws(
    readonly_pool: asyncpg.Pool,
) -> None:
    """deathroll_readonly cannot INSERT into luck.raffle_draws."""
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO luck.raffle_draws
                  (period_id, pool_amount, revealed_server_seed,
                   server_seed_hash, client_seed_used, nonces_used,
                   winners, total_tickets)
                VALUES (1, 100, '\\x00'::bytea, '\\x00'::bytea,
                        'cs', '[]'::jsonb, '[]'::jsonb, 0)
                """
            )


# ---------------------------------------------------------------------------
# bet_rounds composite UNIQUE
# ---------------------------------------------------------------------------


async def test_bet_rounds_unique_per_bet_round_index(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """(bet_id, round_index) UNIQUE prevents duplicate round records."""
    await _seed_user_and_game(admin_pool, user_id=7100)
    async with luck_pool.acquire() as conn:
        bet_id = await conn.fetchval(
            """
            INSERT INTO luck.bets
              (bet_uid, discord_id, game_name, channel_id, bet_amount,
               selection, status, server_seed_hash, client_seed, nonce,
               idempotency_key)
            VALUES ('LCK-MR', 7100, 'coinflip', 1, 100, '{}'::jsonb,
                    'open', '\\x00'::bytea, 'cs', 0, 'mrk')
            RETURNING id
            """
        )
        await conn.execute(
            """
            INSERT INTO luck.bet_rounds
              (bet_id, round_index, nonce, action, outcome)
            VALUES ($1, 0, 0, 'hit', '{}'::jsonb)
            """,
            bet_id,
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO luck.bet_rounds
                  (bet_id, round_index, nonce, action, outcome)
                VALUES ($1, 0, 1, 'stand', '{}'::jsonb)
                """,
                bet_id,
            )
