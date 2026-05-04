"""Story 2.8d — fairness.rotate_user_seed.

Spec ref: Luck design §4.1, §4.2 (provably fair lifecycle).

The contract:

- ``fairness.rotate_user_seed(p_discord_id, p_rotated_by)`` returns
  ``(revealed_server_seed BYTEA, new_server_seed_hash BYTEA)``.

  - First call for a user (no user_seeds row): generate a fresh
    ``server_seed`` (32 random bytes), store
    ``(server_seed, sha256(server_seed), client_seed=random_hex(16),
    nonce=0)``. Return ``(NULL, new_hash)`` because there's no
    prior to reveal.

  - Subsequent call: archive the existing
    ``(server_seed, hash, client_seed, last_nonce, started_at)``
    into ``fairness.history`` (append-only — insert succeeds; no
    UPDATE needed); generate fresh ``server_seed``; reset
    ``nonce = 0``; preserve ``client_seed`` (user-editable
    separately via ``setseed``); update the user_seeds row.
    Return ``(old_server_seed, new_hash)``.

- ``p_rotated_by`` must be in ``('user', 'system', 'admin')`` —
  enforced by the CHECK on ``fairness.history.rotated_by`` and
  surfaced as ``invalid_rotated_by`` when caught upstream.

- The new ``server_seed_hash`` must equal
  ``sha256(new_server_seed)`` — the public commitment.

- The new row's ``nonce`` is 0 (not the carry-over value).

Permission: deathroll_luck and deathroll_dw can EXECUTE (D/W needs
it for v1.x withdraw-bound rotation per spec §3.1);
deathroll_readonly cannot.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# First-time rotation (creates a fresh row)
# ---------------------------------------------------------------------------


async def test_rotate_first_time_creates_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A user with no user_seeds row gets one created on first
    rotation. revealed_server_seed is NULL (nothing to reveal)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30001) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fairness.rotate_user_seed("
            "  p_discord_id := 30001, p_rotated_by := 'user')"
        )
        seed_row = await conn.fetchrow(
            "SELECT server_seed, server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = 30001"
        )

    assert row["revealed_server_seed"] is None
    assert row["new_server_seed_hash"] is not None
    assert seed_row is not None
    assert seed_row["nonce"] == 0
    # client_seed defaults to a 16-char hex string.
    assert len(seed_row["client_seed"]) == 16
    # server_seed is 32 random bytes.
    assert len(seed_row["server_seed"]) == 32
    # The committed hash matches what the SDF returned.
    assert seed_row["server_seed_hash"] == row["new_server_seed_hash"]


async def test_rotate_first_time_no_history_row(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The first rotation does NOT create a history row (nothing to archive)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30002) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        await conn.fetchrow(
            "SELECT * FROM fairness.rotate_user_seed("
            "  p_discord_id := 30002, p_rotated_by := 'user')"
        )
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM fairness.history WHERE discord_id = 30002"
        )
    assert cnt == 0


# ---------------------------------------------------------------------------
# Rotation archives the old seed
# ---------------------------------------------------------------------------


async def test_rotate_subsequent_archives_old_seed(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Subsequent rotation archives the old (server_seed, hash,
    client_seed, last_nonce) into fairness.history."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30010) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        # First rotation: creates the row.
        first = await conn.fetchrow(
            "SELECT * FROM fairness.rotate_user_seed("
            "  p_discord_id := 30010, p_rotated_by := 'user')"
        )
        # Capture the seed material before the second rotation.
        before = await conn.fetchrow(
            "SELECT server_seed, server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = 30010"
        )
        # Bump the nonce a few times to simulate gameplay.
        await conn.execute(
            "UPDATE fairness.user_seeds SET nonce = 7 WHERE discord_id = 30010"
        )
        # Second rotation: archives the old.
        second = await conn.fetchrow(
            "SELECT * FROM fairness.rotate_user_seed("
            "  p_discord_id := 30010, p_rotated_by := 'user')"
        )

    assert first["revealed_server_seed"] is None
    assert second["revealed_server_seed"] == before["server_seed"]
    assert second["new_server_seed_hash"] != before["server_seed_hash"]

    async with admin_pool.acquire() as conn:
        history = await conn.fetchrow(
            "SELECT revealed_server_seed, server_seed_hash, "
            "       client_seed, last_nonce, rotated_by "
            "FROM fairness.history WHERE discord_id = 30010 "
            "ORDER BY id DESC LIMIT 1"
        )
    assert history["revealed_server_seed"] == before["server_seed"]
    assert history["server_seed_hash"] == before["server_seed_hash"]
    assert history["client_seed"] == before["client_seed"]
    assert history["last_nonce"] == 7
    assert history["rotated_by"] == "user"


async def test_rotate_resets_nonce_to_zero(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Post-rotation, the user_seeds.nonce is back at 0."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30011) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30011, p_rotated_by := 'user')"
        )
        await conn.execute(
            "UPDATE fairness.user_seeds SET nonce = 42 WHERE discord_id = 30011"
        )
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30011, p_rotated_by := 'user')"
        )
        nonce = await conn.fetchval(
            "SELECT nonce FROM fairness.user_seeds WHERE discord_id = 30011"
        )
    assert nonce == 0


async def test_rotate_preserves_client_seed(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Rotation preserves the client_seed (only setseed changes it)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30012) "
            "ON CONFLICT DO NOTHING"
        )
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30012, p_rotated_by := 'user')"
        )
        await conn.execute(
            "UPDATE fairness.user_seeds SET client_seed = 'my_lucky_seed' "
            "WHERE discord_id = 30012"
        )
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30012, p_rotated_by := 'user')"
        )
        cs = await conn.fetchval(
            "SELECT client_seed FROM fairness.user_seeds WHERE discord_id = 30012"
        )
    assert cs == "my_lucky_seed"


# ---------------------------------------------------------------------------
# Hash commitment
# ---------------------------------------------------------------------------


async def test_rotate_hash_matches_seed(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The new server_seed_hash equals SHA-256(server_seed). Verifiable
    end-to-end (this is the heart of the commit-reveal model)."""
    import hashlib

    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30020) "
            "ON CONFLICT DO NOTHING"
        )
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30020, p_rotated_by := 'user')"
        )
        row = await conn.fetchrow(
            "SELECT server_seed, server_seed_hash "
            "FROM fairness.user_seeds WHERE discord_id = 30020"
        )

    assert hashlib.sha256(row["server_seed"]).digest() == row["server_seed_hash"]


# ---------------------------------------------------------------------------
# rotated_by validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotated_by", ["user", "system", "admin"])
async def test_rotate_accepts_valid_rotated_by(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool, rotated_by: str
) -> None:
    """All three legal rotated_by values are accepted."""
    user_id = 30030 + hash(rotated_by) % 100
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )
    async with luck_pool.acquire() as conn:
        # Bootstrap the seed first.
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := $1, p_rotated_by := 'user')",
            user_id,
        )
        # Then rotate with the parametrized rotated_by.
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := $1, p_rotated_by := $2)",
            user_id,
            rotated_by,
        )


async def test_rotate_rejects_invalid_rotated_by(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """An invalid rotated_by (not in user/system/admin) is rejected."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30040) "
            "ON CONFLICT DO NOTHING"
        )
    async with luck_pool.acquire() as conn:
        # Bootstrap.
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30040, p_rotated_by := 'user')"
        )
        # Subsequent rotation attempts to archive with bad rotated_by.
        with pytest.raises(asyncpg.exceptions.RaiseError, match="invalid_rotated_by"):
            await conn.execute(
                "SELECT fairness.rotate_user_seed("
                "  p_discord_id := 30040, p_rotated_by := 'OPERATOR')"
            )


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------


async def test_rotate_two_users_independent(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Two users rotating their seeds don't affect each other."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30050), (30051) "
            "ON CONFLICT DO NOTHING"
        )
    async with luck_pool.acquire() as conn:
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30050, p_rotated_by := 'user')"
        )
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30051, p_rotated_by := 'user')"
        )
        a = await conn.fetchval(
            "SELECT server_seed FROM fairness.user_seeds WHERE discord_id = 30050"
        )
        b = await conn.fetchval(
            "SELECT server_seed FROM fairness.user_seeds WHERE discord_id = 30051"
        )
    # Two CSPRNG draws are essentially never equal.
    assert a != b


# ---------------------------------------------------------------------------
# Permission boundary
# ---------------------------------------------------------------------------


async def test_rotate_dw_role_can_execute(
    dw_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_dw has EXECUTE — D/W may rotate seeds during a
    withdraw flow in v1.x (spec §3.1)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (30060) "
            "ON CONFLICT DO NOTHING"
        )
    async with dw_pool.acquire() as conn:
        # Should NOT raise.
        await conn.execute(
            "SELECT fairness.rotate_user_seed("
            "  p_discord_id := 30060, p_rotated_by := 'system')"
        )


async def test_rotate_readonly_no_execute(readonly_pool: asyncpg.Pool) -> None:
    """deathroll_readonly cannot EXECUTE rotate_user_seed."""
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "SELECT fairness.rotate_user_seed("
                "  p_discord_id := 1, p_rotated_by := 'user')"
            )
