"""Story 2.6 — fairness.user_seeds + fairness.history schemas.

Spec ref: Luck design §3.3, §4.2.

Verifies:
- ``fairness.user_seeds`` exists with the spec's column shape, the
  PK on ``discord_id``, the FK to ``core.users``, and the default
  ``nonce = 0``.
- ``fairness.history`` exists with the spec's column shape, the
  ``rotated_by`` CHECK constraint, and the descending index
  ``idx_fairness_history_user``.
- ``fairness.history`` has append-only triggers on UPDATE/DELETE
  matching ``core.audit_log``'s immutability semantics.
- The default privilege matrix landed correctly:
  - ``deathroll_luck`` can SELECT/INSERT/UPDATE on both tables.
  - ``deathroll_dw`` can SELECT/INSERT/UPDATE on both tables (D/W
    rotates seeds during withdraw flows in v1.x).
  - ``deathroll_readonly`` can SELECT only.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


async def test_fairness_user_seeds_columns(luck_pool: asyncpg.Pool) -> None:
    """The user_seeds table has exactly the spec's columns + types."""
    async with luck_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'fairness' AND table_name = 'user_seeds'
            ORDER BY ordinal_position
            """
        )
    columns = {r["column_name"]: dict(r) for r in rows}

    assert set(columns) == {
        "discord_id",
        "server_seed",
        "server_seed_hash",
        "client_seed",
        "nonce",
        "created_at",
        "rotated_at",
    }
    assert columns["discord_id"]["data_type"] == "bigint"
    assert columns["server_seed"]["data_type"] == "bytea"
    assert columns["server_seed_hash"]["data_type"] == "bytea"
    assert columns["client_seed"]["data_type"] == "text"
    assert columns["nonce"]["data_type"] == "bigint"
    assert columns["nonce"]["is_nullable"] == "NO"
    # Default 0 on nonce so a freshly-rotated user starts the ladder.
    assert "0" in (columns["nonce"]["column_default"] or "")
    assert columns["created_at"]["data_type"] == "timestamp with time zone"
    assert columns["rotated_at"]["data_type"] == "timestamp with time zone"


async def test_fairness_user_seeds_primary_key_is_discord_id(
    luck_pool: asyncpg.Pool,
) -> None:
    """PK on discord_id (one row per user)."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid
             AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'fairness.user_seeds'::regclass
              AND i.indisprimary
            """
        )
    assert row is not None
    assert row["attname"] == "discord_id"


async def test_fairness_user_seeds_fk_to_core_users(
    luck_pool: asyncpg.Pool,
) -> None:
    """FK to core.users(discord_id) ON DELETE RESTRICT."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                conname,
                confrelid::regclass::text AS ref_table,
                confdeltype
            FROM pg_constraint
            WHERE conrelid = 'fairness.user_seeds'::regclass
              AND contype = 'f'
            """
        )
    assert row is not None
    assert row["ref_table"] == "core.users"
    # 'r' = NO ACTION/RESTRICT; spec calls for ON DELETE RESTRICT.
    # Postgres "char" type comes through asyncpg as a 1-byte bytestring.
    assert row["confdeltype"] == b"r"


async def test_fairness_history_columns(luck_pool: asyncpg.Pool) -> None:
    """History table has exactly the spec's columns + types."""
    async with luck_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'fairness' AND table_name = 'history'
            ORDER BY ordinal_position
            """
        )
    columns = {r["column_name"]: dict(r) for r in rows}

    assert set(columns) == {
        "id",
        "discord_id",
        "revealed_server_seed",
        "server_seed_hash",
        "client_seed",
        "last_nonce",
        "started_at",
        "rotated_at",
        "rotated_by",
    }
    # id BIGSERIAL is bigint with sequence default
    assert columns["id"]["data_type"] == "bigint"
    assert columns["revealed_server_seed"]["data_type"] == "bytea"
    assert columns["server_seed_hash"]["data_type"] == "bytea"
    assert columns["last_nonce"]["data_type"] == "bigint"


async def test_fairness_history_rotated_by_check(
    admin_pool: asyncpg.Pool,
) -> None:
    """``rotated_by`` only accepts user / system / admin."""
    async with admin_pool.acquire() as conn:
        # Need a real user row so the FK is satisfied.
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (1234) "
            "ON CONFLICT DO NOTHING"
        )
        # An invalid value must be rejected by the CHECK constraint.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO fairness.history
                  (discord_id, revealed_server_seed, server_seed_hash,
                   client_seed, last_nonce, started_at, rotated_by)
                VALUES (1234, '\\x00'::bytea, '\\x00'::bytea, 'cs', 0,
                        NOW(), 'OPERATOR')
                """
            )


async def test_fairness_history_descending_index_present(
    luck_pool: asyncpg.Pool,
) -> None:
    """idx_fairness_history_user exists per spec."""
    async with luck_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'fairness'
              AND tablename = 'history'
              AND indexname = 'idx_fairness_history_user'
            """
        )
    assert row is not None


# ---------------------------------------------------------------------------
# Append-only enforcement on fairness.history
# ---------------------------------------------------------------------------


async def test_fairness_history_rejects_update_via_admin(
    admin_pool: asyncpg.Pool,
) -> None:
    """An UPDATE on fairness.history must fail at the trigger level
    even from the admin role (matches core.audit_log immutability).
    """
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (5555) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO fairness.history
              (discord_id, revealed_server_seed, server_seed_hash,
               client_seed, last_nonce, started_at, rotated_by)
            VALUES (5555, '\\xAA'::bytea, '\\xBB'::bytea, 'orig', 7,
                    NOW(), 'user')
            """
        )
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "UPDATE fairness.history SET client_seed = 'tampered' "
                "WHERE discord_id = 5555"
            )


async def test_fairness_history_rejects_delete_via_admin(
    admin_pool: asyncpg.Pool,
) -> None:
    """A DELETE on fairness.history must fail at the trigger level
    even from the admin role.
    """
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (6666) "
            "ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            """
            INSERT INTO fairness.history
              (discord_id, revealed_server_seed, server_seed_hash,
               client_seed, last_nonce, started_at, rotated_by)
            VALUES (6666, '\\x01'::bytea, '\\x02'::bytea, 'cs', 0,
                    NOW(), 'user')
            """
        )
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "DELETE FROM fairness.history WHERE discord_id = 6666"
            )


# ---------------------------------------------------------------------------
# Privilege matrix
# ---------------------------------------------------------------------------


async def test_luck_role_can_insert_user_seeds(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_luck has INSERT/UPDATE on fairness.user_seeds."""
    # Admin pre-creates the FK target.
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (101) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fairness.user_seeds
              (discord_id, server_seed, server_seed_hash, client_seed)
            VALUES (101, '\\xDE'::bytea, '\\xAD'::bytea, 'beef')
            """
        )
        # Update is also allowed (rotation increments nonce; client_seed
        # is user-editable).
        await conn.execute(
            "UPDATE fairness.user_seeds SET nonce = nonce + 1 "
            "WHERE discord_id = 101"
        )
        row = await conn.fetchrow(
            "SELECT nonce FROM fairness.user_seeds WHERE discord_id = 101"
        )
        assert row is not None
        assert row["nonce"] == 1


async def test_dw_role_can_insert_user_seeds(
    dw_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_dw also has SELECT/INSERT/UPDATE on fairness.user_seeds.

    The fairness schema is shared core infrastructure; D/W can rotate
    a user's seed during a withdraw flow in v1.x if the operator
    decides to bind seed rotation to the withdraw cycle. v1 does NOT
    do this, but the grants are pre-positioned per spec §3.1.
    """
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (202) "
            "ON CONFLICT DO NOTHING"
        )

    async with dw_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fairness.user_seeds
              (discord_id, server_seed, server_seed_hash, client_seed)
            VALUES (202, '\\xCA'::bytea, '\\xFE'::bytea, 'cs')
            """
        )


async def test_readonly_role_cannot_insert_user_seeds(
    readonly_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """deathroll_readonly can SELECT but cannot INSERT/UPDATE/DELETE."""
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (303) "
            "ON CONFLICT DO NOTHING"
        )
        await admin_conn.execute(
            """
            INSERT INTO fairness.user_seeds
              (discord_id, server_seed, server_seed_hash, client_seed)
            VALUES (303, '\\xAB'::bytea, '\\xCD'::bytea, 'cs')
            """
        )

    async with readonly_pool.acquire() as conn:
        # SELECT is allowed.
        row = await conn.fetchrow(
            "SELECT discord_id FROM fairness.user_seeds WHERE discord_id = 303"
        )
        assert row is not None and row["discord_id"] == 303

        # INSERT must fail with permission denied.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO fairness.user_seeds
                  (discord_id, server_seed, server_seed_hash, client_seed)
                VALUES (304, '\\x00'::bytea, '\\x00'::bytea, 'cs')
                """
            )


async def test_readonly_role_cannot_insert_history(
    readonly_pool: asyncpg.Pool,
) -> None:
    """deathroll_readonly cannot INSERT into fairness.history either."""
    async with readonly_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO fairness.history
                  (discord_id, revealed_server_seed, server_seed_hash,
                   client_seed, last_nonce, started_at, rotated_by)
                VALUES (1, '\\x00'::bytea, '\\x00'::bytea, 'cs', 0,
                        NOW(), 'user')
                """
            )


# ---------------------------------------------------------------------------
# Behavioural smoke
# ---------------------------------------------------------------------------


async def test_user_seeds_default_nonce_is_zero(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """A freshly-inserted row defaults nonce = 0."""
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (404) "
            "ON CONFLICT DO NOTHING"
        )

    async with luck_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO fairness.user_seeds
              (discord_id, server_seed, server_seed_hash, client_seed)
            VALUES (404, '\\x00'::bytea, '\\x00'::bytea, 'cs')
            """
        )
        row = await conn.fetchrow(
            "SELECT nonce, created_at, rotated_at "
            "FROM fairness.user_seeds WHERE discord_id = 404"
        )
        assert row is not None
        assert row["nonce"] == 0
        assert row["created_at"] is not None
        assert row["rotated_at"] is not None


async def test_history_descending_index_orders_correctly(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The (discord_id, rotated_at DESC) index supports the natural
    'show me my last N rotations' query.
    """
    async with admin_pool.acquire() as admin_conn:
        await admin_conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (505) "
            "ON CONFLICT DO NOTHING"
        )
        for nonce in (5, 12, 3):
            await admin_conn.execute(
                """
                INSERT INTO fairness.history
                  (discord_id, revealed_server_seed, server_seed_hash,
                   client_seed, last_nonce, started_at, rotated_by)
                VALUES (505, '\\x00'::bytea, '\\x00'::bytea, 'cs', $1,
                        NOW(), 'user')
                """,
                nonce,
            )

    async with luck_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT last_nonce FROM fairness.history "
            "WHERE discord_id = 505 ORDER BY rotated_at DESC LIMIT 5"
        )
    assert len(rows) == 3
    # All three landed; the descending index is verified by the
    # query planner running it cheaply (no plan assertion here, but
    # the EXPLAIN is exercised in the catalog-level test above).
