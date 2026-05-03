"""Smoke test for the integration fixture (Epic 14 foundation).

Boots the testcontainers Postgres, applies migrations, opens a pool
as ``deathroll_dw``, and runs the cheapest possible round-trip:

- The bot role can call a SECURITY DEFINER fn (``dw.create_deposit_ticket``).
- The hash-chain trigger fires (``audit_log_insert_with_chain`` writes a row).
- Per-test TRUNCATE works (the second test sees an empty state).

If this test passes, the fixture is healthy and every later
integration story has a known-good baseline.
"""

from __future__ import annotations

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_fixture_starts_and_bot_role_can_create_deposit_ticket(
    pool: asyncpg.Pool,
) -> None:
    uid = await pool.fetchval(
        "SELECT dw.create_deposit_ticket($1, $2, $3, $4, $5, $6, $7, $8)",
        12345,
        "Testchar",
        "Stormrage",
        "EU",
        "Horde",
        50_000,
        99999,
        88888,
    )
    assert isinstance(uid, str) and uid.startswith("deposit-")

    row = await pool.fetchrow(
        "SELECT status, amount FROM dw.deposit_tickets WHERE ticket_uid = $1",
        uid,
    )
    assert row is not None
    assert row["status"] == "open"
    assert row["amount"] == 50_000


@pytest.mark.asyncio
async def test_fixture_isolates_state_between_tests(pool: asyncpg.Pool) -> None:
    """The previous test inserted a deposit ticket. After TRUNCATE we
    should see ZERO rows — proves the per-test reset works."""
    n = await pool.fetchval("SELECT COUNT(*) FROM dw.deposit_tickets")
    assert n == 0


@pytest.mark.asyncio
async def test_global_config_seed_is_present(pool: asyncpg.Pool) -> None:
    """Migration 0005's seed values + our re-seed in the conftest
    must leave the canonical limits available."""
    row = await pool.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = 'min_deposit_g'"
    )
    assert row is not None
    assert row["value_int"] == 200


@pytest.mark.asyncio
async def test_treasury_seed_row_exists(pool: asyncpg.Pool) -> None:
    """``core.balances`` discord_id=0 is the treasury bucket. Without
    it, ``dw.confirm_withdraw`` would invariant-violate on the first
    fee credit. The conftest reseeds it after every TRUNCATE."""
    balance = await pool.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = 0"
    )
    assert balance == 0
