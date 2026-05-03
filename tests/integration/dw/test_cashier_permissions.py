"""Cashier permission tests (Story 14.4).

Spec §8.2 ACs:

- Only the claimer can ``confirm`` (cashier B's confirm on
  cashier A's ticket → wrong_cashier).
- Region mismatch on claim refused (EU-only cashier claiming
  an NA ticket → region_mismatch).
- Non-cashier cannot invoke `/cashier-*` or `/claim` commands.

The third AC is enforced at the Discord-side
(``@app_commands.default_permissions(...)`` and Server-Settings
role visibility) — not at the SDF layer. We unit-test that
boundary already in ``tests/unit/dw/test_admin_cog.py``
("hidden from non-admins by default" assertion). Here we focus
on the two SDF-level gates (wrong_cashier + region_mismatch)
which are the actual security invariants the DB enforces.
"""

from __future__ import annotations

import asyncpg
import pytest
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    apply_deposit_ticket,
    claim_ticket,
    confirm_deposit,
    release_ticket,
)


_USER = 33333


# ---------------------------------------------------------------------------
# Helper: register one cashier with EU/Horde chars, online.
# ---------------------------------------------------------------------------


async def _register_cashier(
    pool: asyncpg.Pool,
    *,
    cashier_id: int,
    region: str,
    faction: str = "Horde",
) -> None:
    await pool.execute(
        "INSERT INTO dw.cashier_characters "
        "(discord_id, char_name, realm, region, faction) "
        "VALUES ($1, $2, 'Stormrage', $3, $4)",
        cashier_id,
        f"Cashier{cashier_id}",
        region,
        faction,
    )
    await pool.execute(
        "INSERT INTO dw.cashier_status "
        "(discord_id, status, set_at, last_active_at) "
        "VALUES ($1, 'online', NOW(), NOW()) "
        "ON CONFLICT (discord_id) DO UPDATE SET status = 'online'",
        cashier_id,
    )


# ---------------------------------------------------------------------------
# wrong_cashier — confirm fails when the caller didn't claim the ticket.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_by_non_claimer_rejected_with_wrong_cashier(
    pool: asyncpg.Pool,
) -> None:
    """Cashier A claims; cashier B's confirm raises wrong_cashier."""
    cashier_a, cashier_b = 9001, 9002
    await _register_cashier(pool, cashier_id=cashier_a, region="EU")
    await _register_cashier(pool, cashier_id=cashier_b, region="EU")

    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=1,
        parent_channel_id=2,
    )
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=uid, cashier_id=cashier_a
    )

    with pytest.raises(exc.WrongCashier):
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=cashier_b)


@pytest.mark.asyncio
async def test_release_by_non_claimer_rejected_with_wrong_cashier(
    pool: asyncpg.Pool,
) -> None:
    """release also enforces the claimer identity."""
    cashier_a, cashier_b = 9001, 9002
    await _register_cashier(pool, cashier_id=cashier_a, region="EU")
    await _register_cashier(pool, cashier_id=cashier_b, region="EU")

    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=3,
        parent_channel_id=4,
    )
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=uid, cashier_id=cashier_a
    )

    with pytest.raises(exc.WrongCashier):
        await release_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, actor_id=cashier_b
        )


# ---------------------------------------------------------------------------
# region_mismatch — claim fails when the cashier has no active char in
# the ticket's region.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eu_only_cashier_claiming_na_ticket_rejected(
    pool: asyncpg.Pool,
) -> None:
    """User opens an NA ticket. Only-EU cashier's claim → region_mismatch."""
    cashier = 9001
    await _register_cashier(pool, cashier_id=cashier, region="EU")

    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormwind",
        region="NA",
        faction="Alliance",
        amount=10_000,
        thread_id=5,
        parent_channel_id=6,
    )
    with pytest.raises(exc.RegionMismatch):
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=cashier
        )


@pytest.mark.asyncio
async def test_cashier_with_chars_in_both_regions_can_claim_either(
    pool: asyncpg.Pool,
) -> None:
    """Multi-region cashier — registers chars in EU AND NA.
    Claim succeeds for both ticket regions."""
    cashier = 9001
    await _register_cashier(pool, cashier_id=cashier, region="EU")
    # Register a second char in NA for the same cashier.
    await pool.execute(
        "INSERT INTO dw.cashier_characters "
        "(discord_id, char_name, realm, region, faction) "
        "VALUES ($1, 'CashierNA', 'Stormwind', 'NA', 'Alliance')",
        cashier,
    )

    eu_uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=7,
        parent_channel_id=8,
    )
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=eu_uid, cashier_id=cashier
    )

    na_uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER + 1,  # different user so balance check passes
        char_name="C2",
        realm="Stormwind",
        region="NA",
        faction="Alliance",
        amount=10_000,
        thread_id=9,
        parent_channel_id=10,
    )
    await claim_ticket(
        pool, ticket_type="deposit", ticket_uid=na_uid, cashier_id=cashier
    )

    # Both tickets land in 'claimed' under the same cashier id.
    rows = await pool.fetch(
        "SELECT ticket_uid, status, claimed_by FROM dw.deposit_tickets "
        "WHERE ticket_uid IN ($1, $2)",
        eu_uid,
        na_uid,
    )
    assert {r["status"] for r in rows} == {"claimed"}
    assert {r["claimed_by"] for r in rows} == {cashier}


@pytest.mark.asyncio
async def test_cashier_with_no_active_chars_rejected_with_region_mismatch(
    pool: asyncpg.Pool,
) -> None:
    """If a cashier has rows in cashier_characters but they're all
    soft-deleted (``is_active = FALSE``), the SDF treats them as
    region-less — claim raises region_mismatch.

    Note: the SDF checks ``is_active = TRUE``, NOT ``cashier_status =
    'online'``. The online status is roster-display only; claim
    authorization is at the character level. This matches the
    deliberate design of allowing a cashier to claim while in 'break'
    or even 'offline' states (the worker auto-offlines stale
    sessions, but in-flight claims aren't blocked on it).
    """
    cashier = 9001
    await _register_cashier(pool, cashier_id=cashier, region="EU")
    # Soft-delete the char.
    await pool.execute(
        "UPDATE dw.cashier_characters SET is_active=FALSE, removed_at=NOW() "
        "WHERE discord_id=$1",
        cashier,
    )

    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=10_000,
        thread_id=11,
        parent_channel_id=12,
    )
    with pytest.raises(exc.RegionMismatch):
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=cashier
        )
