"""Story 2.8c — luck.consume_rate_token + fairness.next_nonce +
luck.grant_raffle_tickets.

Spec ref: Luck design §3.4 (stored procedures — economic boundary).

Three small helper SDFs:

- ``luck.consume_rate_token(p_discord_id, p_scope, p_window_s,
  p_max_count)`` — atomic increment-and-check of a sliding bucket.
  Returns TRUE if the action is allowed, FALSE if the user has
  already hit the cap inside the current window.

- ``fairness.next_nonce(p_discord_id)`` — atomically increments
  ``fairness.user_seeds.nonce`` and returns the value the caller
  should USE (i.e., the nonce as it was BEFORE the increment).
  Used by every game's outcome derivation as the nonce input to
  HMAC-SHA512.

- ``luck.grant_raffle_tickets(p_discord_id, p_period_id,
  p_ticket_count, p_bet_id)`` — inserts ``p_ticket_count`` rows
  in ``luck.raffle_tickets`` for the given period+user, all
  pointing at the originating ``p_bet_id``. Returns the number
  inserted.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# luck.consume_rate_token
# ---------------------------------------------------------------------------


async def test_rate_limit_allows_first_call(luck_pool: asyncpg.Pool) -> None:
    """First call inside a fresh window returns TRUE (allowed)."""
    async with luck_pool.acquire() as conn:
        ok = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 1, p_scope := 'test', "
            "  p_window_s := 60, p_max_count := 3)"
        )
    assert ok is True


async def test_rate_limit_allows_up_to_max(luck_pool: asyncpg.Pool) -> None:
    """Exactly max_count calls are allowed; the next is blocked."""
    async with luck_pool.acquire() as conn:
        for _ in range(3):
            ok = await conn.fetchval(
                "SELECT luck.consume_rate_token("
                "  p_discord_id := 2, p_scope := 'test', "
                "  p_window_s := 60, p_max_count := 3)"
            )
            assert ok is True
        # 4th call within the same 60s window is blocked.
        blocked = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 2, p_scope := 'test', "
            "  p_window_s := 60, p_max_count := 3)"
        )
    assert blocked is False


async def test_rate_limit_isolated_per_scope(luck_pool: asyncpg.Pool) -> None:
    """Different scopes have independent buckets."""
    async with luck_pool.acquire() as conn:
        for _ in range(3):
            await conn.fetchval(
                "SELECT luck.consume_rate_token("
                "  p_discord_id := 3, p_scope := 'bet', "
                "  p_window_s := 60, p_max_count := 3)"
            )
        # 4th in 'bet' scope is blocked.
        blocked = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 3, p_scope := 'bet', "
            "  p_window_s := 60, p_max_count := 3)"
        )
        assert blocked is False
        # But a different scope is fresh.
        ok = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 3, p_scope := 'cmd', "
            "  p_window_s := 60, p_max_count := 3)"
        )
    assert ok is True


async def test_rate_limit_isolated_per_user(luck_pool: asyncpg.Pool) -> None:
    """Different users have independent buckets even on the same scope."""
    async with luck_pool.acquire() as conn:
        for _ in range(3):
            await conn.fetchval(
                "SELECT luck.consume_rate_token("
                "  p_discord_id := 4, p_scope := 'bet', "
                "  p_window_s := 60, p_max_count := 3)"
            )
        # User 4 blocked.
        blocked = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 4, p_scope := 'bet', "
            "  p_window_s := 60, p_max_count := 3)"
        )
        assert blocked is False
        # User 5 fresh.
        ok = await conn.fetchval(
            "SELECT luck.consume_rate_token("
            "  p_discord_id := 5, p_scope := 'bet', "
            "  p_window_s := 60, p_max_count := 3)"
        )
    assert ok is True


async def test_rate_limit_concurrent_is_atomic(luck_pool: asyncpg.Pool) -> None:
    """Parallel calls to the same (user, scope) are serialised correctly:
    exactly max_count return TRUE, the rest return FALSE."""
    max_count = 5

    async def call_once() -> bool:
        async with luck_pool.acquire() as conn:
            v = await conn.fetchval(
                "SELECT luck.consume_rate_token("
                "  p_discord_id := 6, p_scope := 'race', "
                "  p_window_s := 60, p_max_count := 5)"
            )
            return bool(v)

    # Fire 12 calls in parallel; expect exactly 5 TRUE.
    results = await asyncio.gather(*(call_once() for _ in range(12)))
    truthy = sum(1 for r in results if r)
    assert truthy == max_count, f"expected {max_count} allowed, got {truthy}"


# ---------------------------------------------------------------------------
# fairness.next_nonce
# ---------------------------------------------------------------------------


async def _seed_user_seed(
    admin_pool: asyncpg.Pool, *, user_id: int, nonce: int = 0
) -> None:
    """Create a user + a fresh user_seeds row at the given starting nonce."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO fairness.user_seeds
              (discord_id, server_seed, server_seed_hash, client_seed, nonce)
            VALUES ($1, '\\x00'::bytea, '\\x00'::bytea, 'cs', $2)
            ON CONFLICT (discord_id) DO UPDATE
              SET nonce = $2
            """,
            user_id,
            nonce,
        )


async def test_next_nonce_returns_current_and_increments(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """next_nonce returns the value to USE (pre-increment), and the
    stored nonce becomes that value + 1."""
    await _seed_user_seed(admin_pool, user_id=10, nonce=0)

    async with luck_pool.acquire() as conn:
        used0 = await conn.fetchval(
            "SELECT fairness.next_nonce(p_discord_id := 10)"
        )
        used1 = await conn.fetchval(
            "SELECT fairness.next_nonce(p_discord_id := 10)"
        )
        used2 = await conn.fetchval(
            "SELECT fairness.next_nonce(p_discord_id := 10)"
        )
        stored = await conn.fetchval(
            "SELECT nonce FROM fairness.user_seeds WHERE discord_id = 10"
        )

    # Caller used 0, 1, 2 across three calls; stored nonce now = 3.
    assert used0 == 0
    assert used1 == 1
    assert used2 == 2
    assert stored == 3


async def test_next_nonce_unknown_user_raises(luck_pool: asyncpg.Pool) -> None:
    """A user with no fairness.user_seeds row raises 'seed_not_found'."""
    async with luck_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="seed_not_found"):
            await conn.execute(
                "SELECT fairness.next_nonce(p_discord_id := 999999)"
            )


async def test_next_nonce_concurrent_returns_unique_values(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Parallel next_nonce calls each get a unique nonce — no two
    callers see the same value (atomic increment)."""
    await _seed_user_seed(admin_pool, user_id=11, nonce=0)

    async def call_once() -> int:
        async with luck_pool.acquire() as conn:
            v = await conn.fetchval(
                "SELECT fairness.next_nonce(p_discord_id := 11)"
            )
        return int(v)

    results = await asyncio.gather(*(call_once() for _ in range(20)))
    assert len(set(results)) == 20
    assert min(results) == 0
    assert max(results) == 19


# ---------------------------------------------------------------------------
# luck.grant_raffle_tickets
# ---------------------------------------------------------------------------


async def test_grant_raffle_tickets_inserts_n_rows(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """N tickets requested → N rows inserted in luck.raffle_tickets."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (20) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES (1, 'tkt-test',
                    '2026-05-01T00:00Z',
                    '2026-05-31T23:59Z',
                    'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """
        )

    async with luck_pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT luck.grant_raffle_tickets("
            "  p_discord_id := 20, p_period_id := 1, "
            "  p_ticket_count := 5, p_bet_id := NULL)"
        )
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM luck.raffle_tickets "
            "WHERE period_id = 1 AND discord_id = 20"
        )
    assert n == 5
    assert cnt == 5


async def test_grant_raffle_tickets_links_bet_id(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """bet_id is recorded on every granted ticket row."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (21) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO core.balances (discord_id, balance) "
            "VALUES (21, 10000) ON CONFLICT (discord_id) "
            "DO UPDATE SET balance = 10000"
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES (2, 'tkt-test-bet',
                    '2026-05-01T00:00Z',
                    '2026-05-31T23:59Z',
                    'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """
        )
    # Place an actual bet so we have a real bet_id to link.
    async with luck_pool.acquire() as conn:
        bet_row = await conn.fetchrow(
            """
            SELECT * FROM luck.apply_bet(
              p_discord_id := 21, p_game_name := 'coinflip',
              p_channel_id := 0, p_bet_amount := 100,
              p_selection := '{}'::jsonb,
              p_server_seed_hash := '\\x00'::bytea,
              p_client_seed := 'cs', p_nonce := 0,
              p_idempotency_key := 'k', p_bet_uid := 'BET-T2')
            """
        )
        bet_id = bet_row["bet_id"]
        await conn.execute(
            "SELECT luck.grant_raffle_tickets("
            "  p_discord_id := 21, p_period_id := 2, "
            "  p_ticket_count := 3, p_bet_id := $1)",
            bet_id,
        )
        rows = await conn.fetch(
            "SELECT bet_id FROM luck.raffle_tickets "
            "WHERE period_id = 2 AND discord_id = 21"
        )
    assert len(rows) == 3
    assert all(r["bet_id"] == bet_id for r in rows)


async def test_grant_raffle_tickets_zero_returns_zero(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A zero count is a no-op (returns 0, no rows inserted)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (22) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES (3, 'tkt-test-zero',
                    '2026-05-01T00:00Z',
                    '2026-05-31T23:59Z',
                    'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """
        )
    async with luck_pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT luck.grant_raffle_tickets("
            "  p_discord_id := 22, p_period_id := 3, "
            "  p_ticket_count := 0, p_bet_id := NULL)"
        )
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM luck.raffle_tickets WHERE period_id = 3"
        )
    assert n == 0
    assert cnt == 0


async def test_grant_raffle_tickets_negative_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Negative ticket_count is rejected."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (23) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES (4, 'tkt-test-neg',
                    '2026-05-01T00:00Z',
                    '2026-05-31T23:59Z',
                    'active')
            ON CONFLICT (id) DO UPDATE SET status = 'active'
            """
        )
    async with luck_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.RaiseError, match="invalid_ticket_count"
        ):
            await conn.execute(
                "SELECT luck.grant_raffle_tickets("
                "  p_discord_id := 23, p_period_id := 4, "
                "  p_ticket_count := -1, p_bet_id := NULL)"
            )


async def test_grant_raffle_tickets_unknown_period_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Period must exist."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (24) "
            "ON CONFLICT DO NOTHING"
        )
    async with luck_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.RaiseError, match="period_not_found"
        ):
            await conn.execute(
                "SELECT luck.grant_raffle_tickets("
                "  p_discord_id := 24, p_period_id := 99999, "
                "  p_ticket_count := 1, p_bet_id := NULL)"
            )


async def test_grant_raffle_tickets_inactive_period_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Tickets can only be granted to an 'active' period."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (25) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO luck.raffle_periods
              (id, period_label, starts_at, ends_at, status)
            VALUES (5, 'tkt-closed',
                    '2026-04-01T00:00Z',
                    '2026-04-30T23:59Z',
                    'closed')
            ON CONFLICT (id) DO UPDATE SET status = 'closed'
            """
        )
    async with luck_pool.acquire() as conn:
        with pytest.raises(
            asyncpg.exceptions.RaiseError, match="period_not_active"
        ):
            await conn.execute(
                "SELECT luck.grant_raffle_tickets("
                "  p_discord_id := 25, p_period_id := 5, "
                "  p_ticket_count := 1, p_bet_id := NULL)"
            )


# ---------------------------------------------------------------------------
# Permission boundary
# ---------------------------------------------------------------------------


async def test_consume_rate_token_readonly_no_execute(
    readonly_pool: asyncpg.Pool,
) -> None:
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT luck.consume_rate_token("
                "  p_discord_id := 1, p_scope := 't', "
                "  p_window_s := 60, p_max_count := 3)"
            )


async def test_next_nonce_readonly_no_execute(
    readonly_pool: asyncpg.Pool,
) -> None:
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT fairness.next_nonce(p_discord_id := 1)"
            )


async def test_grant_raffle_tickets_readonly_no_execute(
    readonly_pool: asyncpg.Pool,
) -> None:
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT luck.grant_raffle_tickets("
                "  p_discord_id := 1, p_period_id := 1, "
                "  p_ticket_count := 1, p_bet_id := NULL)"
            )
