"""Story 3.2 — integration tests for the seeds.py async wrapper.

Spec ref: Luck design §4.2, §4.3.

Exercises ``deathroll_core.fairness.seeds`` against a real
testcontainers Postgres so the SDF interactions
(``fairness.rotate_user_seed``, direct SELECTs/UPDATEs on
``fairness.user_seeds``) are end-to-end-verified.
"""

from __future__ import annotations

import hashlib

import asyncpg
import pytest
from deathroll_core.fairness.seeds import (
    SeedState,
    ensure_seeds,
    get_public_state,
    rotate,
    set_client_seed,
)

pytestmark = pytest.mark.asyncio


async def _seed_user(admin_pool: asyncpg.Pool, *, user_id: int) -> None:
    """Create the core.users row so the FK on fairness.user_seeds
    is satisfied."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )


# ---------------------------------------------------------------------------
# ensure_seeds
# ---------------------------------------------------------------------------


async def test_ensure_seeds_bootstraps_first_call(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """First call creates the user_seeds row and returns the public state."""
    await _seed_user(admin_pool, user_id=40001)
    state = await ensure_seeds(luck_pool, discord_id=40001)
    assert isinstance(state, SeedState)
    assert state.nonce == 0
    assert len(state.server_seed_hash) == 32
    assert len(state.client_seed) == 16  # default 16-char hex


async def test_ensure_seeds_idempotent(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Subsequent calls are noop — same state, same row."""
    await _seed_user(admin_pool, user_id=40002)
    a = await ensure_seeds(luck_pool, discord_id=40002)
    b = await ensure_seeds(luck_pool, discord_id=40002)
    assert a == b


async def test_ensure_seeds_does_not_archive(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Idempotent ensure_seeds should NOT add history rows after
    the first bootstrap (otherwise it would silently rotate)."""
    await _seed_user(admin_pool, user_id=40003)
    await ensure_seeds(luck_pool, discord_id=40003)
    await ensure_seeds(luck_pool, discord_id=40003)
    await ensure_seeds(luck_pool, discord_id=40003)
    async with admin_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM fairness.history WHERE discord_id = 40003"
        )
    assert cnt == 0


# ---------------------------------------------------------------------------
# get_public_state
# ---------------------------------------------------------------------------


async def test_get_public_state_returns_state(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user(admin_pool, user_id=40010)
    bootstrap = await ensure_seeds(luck_pool, discord_id=40010)
    fetched = await get_public_state(luck_pool, discord_id=40010)
    assert fetched == bootstrap


async def test_get_public_state_returns_none_for_unknown(
    luck_pool: asyncpg.Pool,
) -> None:
    state = await get_public_state(luck_pool, discord_id=99999991)
    assert state is None


# ---------------------------------------------------------------------------
# set_client_seed
# ---------------------------------------------------------------------------


async def test_set_client_seed_updates_the_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user(admin_pool, user_id=40020)
    await ensure_seeds(luck_pool, discord_id=40020)
    new_state = await set_client_seed(
        luck_pool, discord_id=40020, new_client_seed="my-favourite_seed"
    )
    assert new_state.client_seed == "my-favourite_seed"


async def test_set_client_seed_does_not_reset_nonce(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Per spec §4.2: setseed updates client_seed; nonce continues."""
    await _seed_user(admin_pool, user_id=40021)
    await ensure_seeds(luck_pool, discord_id=40021)
    # Manually advance the nonce as gameplay would.
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "UPDATE fairness.user_seeds SET nonce = 25 WHERE discord_id = 40021"
        )
    state = await set_client_seed(
        luck_pool, discord_id=40021, new_client_seed="rolled_25_times"
    )
    assert state.nonce == 25


async def test_set_client_seed_validates_invalid(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user(admin_pool, user_id=40022)
    await ensure_seeds(luck_pool, discord_id=40022)
    with pytest.raises(ValueError, match="invalid client_seed"):
        await set_client_seed(
            luck_pool, discord_id=40022, new_client_seed="bad seed!"
        )


async def test_set_client_seed_unknown_user_raises(
    luck_pool: asyncpg.Pool,
) -> None:
    with pytest.raises(LookupError, match="seed_not_found"):
        await set_client_seed(
            luck_pool, discord_id=99999992, new_client_seed="x"
        )


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


async def test_rotate_first_time_returns_no_prior(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """First rotation has no prior to reveal."""
    await _seed_user(admin_pool, user_id=40030)
    result = await rotate(
        luck_pool, discord_id=40030, rotated_by="user"
    )
    assert result.revealed_server_seed is None
    assert isinstance(result.new_state, SeedState)
    assert result.new_state.nonce == 0


async def test_rotate_second_time_reveals_old_seed(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The revealed seed satisfies SHA-256(revealed) == prior_hash —
    the heart of the commit-reveal model."""
    await _seed_user(admin_pool, user_id=40031)
    first = await rotate(
        luck_pool, discord_id=40031, rotated_by="user"
    )
    prior_hash = first.new_state.server_seed_hash

    second = await rotate(
        luck_pool, discord_id=40031, rotated_by="user"
    )
    assert second.revealed_server_seed is not None
    # The revealed seed's SHA-256 must equal what was previously
    # committed.
    computed_hash = hashlib.sha256(
        second.revealed_server_seed
    ).digest()
    assert computed_hash == prior_hash


async def test_rotate_resets_nonce_to_zero(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user(admin_pool, user_id=40032)
    await ensure_seeds(luck_pool, discord_id=40032)
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "UPDATE fairness.user_seeds SET nonce = 99 WHERE discord_id = 40032"
        )
    result = await rotate(
        luck_pool, discord_id=40032, rotated_by="admin"
    )
    assert result.new_state.nonce == 0


async def test_rotate_invalid_rotated_by_raises(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _seed_user(admin_pool, user_id=40033)
    with pytest.raises(ValueError, match="invalid_rotated_by"):
        await rotate(
            luck_pool, discord_id=40033, rotated_by="OPERATOR"
        )
