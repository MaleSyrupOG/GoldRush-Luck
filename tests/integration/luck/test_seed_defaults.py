"""Story 2.9 — default game_config + global_config seed values.

Spec ref: Luck design §3.3 + §10 (locked decisions).

Verifies that migration 0021 inserts the locked v1 economic
configuration:

- All 9 playable games have a luck.game_config row with the
  uniform defaults (house_edge_bps=500, min_bet=100,
  max_bet=500_000, enabled=TRUE).
- Game-specific extra_config knobs match spec for blackjack,
  roulette, mines.
- Flower Poker is explicitly absent (was excluded by collaborator
  decision 2026-04-29).
- luck.global_config has the four governance rows
  (raffle_rake_bps=100, raffle_ticket_threshold_g=100,
  bet_rate_limit_per_60s=30, command_rate_limit_per_60s=30).
- Idempotency: re-running the seed (the conftest re-runs it via
  ON CONFLICT after each TRUNCATE) doesn't multiply rows.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


_EXPECTED_GAMES = {
    "coinflip",
    "dice",
    "ninetyninex",
    "hotcold",
    "mines",
    "blackjack",
    "roulette",
    "diceduel",
    "stakingduel",
}


# ---------------------------------------------------------------------------
# game_config presence + uniform defaults
# ---------------------------------------------------------------------------


async def test_all_nine_games_seeded(luck_pool: asyncpg.Pool) -> None:
    """Every spec'd game has a row; nothing else."""
    async with luck_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT game_name FROM luck.game_config ORDER BY game_name"
        )
    seen = {r["game_name"] for r in rows}
    assert seen == _EXPECTED_GAMES


async def test_flower_poker_absent(luck_pool: asyncpg.Pool) -> None:
    """Flower Poker was excluded per collaborator decision 2026-04-29."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM luck.game_config WHERE game_name = 'flower_poker'"
        )
    assert row is None


@pytest.mark.parametrize("game_name", sorted(_EXPECTED_GAMES))
async def test_game_uniform_defaults(
    luck_pool: asyncpg.Pool, game_name: str
) -> None:
    """Every seeded game has the locked v1 defaults."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT enabled, min_bet, max_bet, house_edge_bps
            FROM luck.game_config WHERE game_name = $1
            """,
            game_name,
        )
    assert row is not None
    assert row["enabled"] is True
    assert row["min_bet"] == 100
    assert row["max_bet"] == 500_000
    assert row["house_edge_bps"] == 500


# ---------------------------------------------------------------------------
# Game-specific extra_config knobs
# ---------------------------------------------------------------------------


async def test_blackjack_extra_config(luck_pool: asyncpg.Pool) -> None:
    """Blackjack ships with the vegas-rules + 4.5 % commission."""
    async with luck_pool.acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT extra_config FROM luck.game_config "
            "WHERE game_name = 'blackjack'"
        )
    import json

    parsed = json.loads(cfg)
    assert parsed == {
        "commission_bps": 450,
        "rules": "vegas_s17_3to2_noins_nosplit",
        "decks": 6,
    }


async def test_roulette_extra_config(luck_pool: asyncpg.Pool) -> None:
    """Roulette ships with european-single-zero + 2.36 % commission."""
    async with luck_pool.acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT extra_config FROM luck.game_config "
            "WHERE game_name = 'roulette'"
        )
    import json

    parsed = json.loads(cfg)
    assert parsed == {
        "commission_bps": 236,
        "variant": "european_single_zero",
    }


async def test_mines_extra_config(luck_pool: asyncpg.Pool) -> None:
    """Mines ships with the 5x5 grid and 1-24 mines range."""
    async with luck_pool.acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT extra_config FROM luck.game_config "
            "WHERE game_name = 'mines'"
        )
    import json

    parsed = json.loads(cfg)
    assert parsed == {
        "max_mines": 24,
        "min_mines": 1,
        "default_mines": 3,
        "grid_size": 25,
    }


@pytest.mark.parametrize(
    "game_name", ["coinflip", "dice", "ninetyninex", "hotcold", "diceduel", "stakingduel"]
)
async def test_parametric_games_have_empty_extra_config(
    luck_pool: asyncpg.Pool, game_name: str
) -> None:
    """Parametric games (the 5 % edge baked into payout) carry an
    empty ``extra_config`` per spec §3.3."""
    async with luck_pool.acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT extra_config FROM luck.game_config WHERE game_name = $1",
            game_name,
        )
    import json

    parsed = json.loads(cfg)
    assert parsed == {}


# ---------------------------------------------------------------------------
# global_config seeds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("raffle_rake_bps", 100),
        ("raffle_ticket_threshold_g", 100),
        ("bet_rate_limit_per_60s", 30),
        ("command_rate_limit_per_60s", 30),
    ],
)
async def test_global_config_seed(
    luck_pool: asyncpg.Pool, key: str, value: int
) -> None:
    """Each governance constant lands at the locked v1 value."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value_int FROM luck.global_config WHERE key = $1",
            key,
        )
    assert row is not None, f"global_config[{key}] missing"
    assert row["value_int"] == value


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_seed_is_idempotent_via_conflict(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Running the same seed INSERT twice doesn't duplicate rows.

    The migration uses ON CONFLICT DO NOTHING. We re-execute the
    seed manually as the admin role and verify the row count for
    coinflip stays at 1.
    """
    async with admin_pool.acquire() as conn:
        # Re-run the same INSERT shape the migration uses.
        await conn.execute(
            """
            INSERT INTO luck.game_config
              (game_name, enabled, min_bet, max_bet, house_edge_bps,
               extra_config, updated_by)
            VALUES ('coinflip', TRUE, 100, 500000, 500, '{}'::jsonb, 0)
            ON CONFLICT (game_name) DO NOTHING
            """
        )

    async with luck_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM luck.game_config WHERE game_name = 'coinflip'"
        )
    assert cnt == 1


async def test_seed_does_not_overwrite_operator_tuned_values(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """If an admin tunes max_bet for coinflip, re-running the seed
    does NOT clobber the tuned value (ON CONFLICT DO NOTHING)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "UPDATE luck.game_config SET max_bet = 1234567 "
            "WHERE game_name = 'coinflip'"
        )
        # Try the same seed again.
        await conn.execute(
            """
            INSERT INTO luck.game_config
              (game_name, enabled, min_bet, max_bet, house_edge_bps,
               extra_config, updated_by)
            VALUES ('coinflip', TRUE, 100, 500000, 500, '{}'::jsonb, 0)
            ON CONFLICT (game_name) DO NOTHING
            """
        )

    async with luck_pool.acquire() as conn:
        max_bet = await conn.fetchval(
            "SELECT max_bet FROM luck.game_config WHERE game_name = 'coinflip'"
        )
    # The operator's tune survives.
    assert max_bet == 1234567
