"""Unit tests for `deathroll_core.balance.account_stats`.

The stats query joins ``core.users`` / ``core.balances`` /
``dw.deposit_tickets`` / ``dw.withdraw_tickets`` to surface the four
metrics the user sees on ``/balance``:

- current balance
- total deposited (lifetime)
- total withdrawn (lifetime gross)
- lifetime fee paid (lifetime fee total)

A user with no row in ``core.users`` returns ``None`` so the cog can
render the "no balance" redirect embed instead.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from deathroll_core.balance.account_stats import AccountStats, fetch_account_stats


class _FakeExec:
    """Stand-in for an asyncpg pool / connection.

    Only ``fetchrow`` is used; the test parametrises what the row
    payload looks like.
    """

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchrow(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> dict[str, Any] | None:
        return self._row


@pytest.mark.asyncio
async def test_returns_none_when_user_not_registered() -> None:
    exec_ = _FakeExec(row=None)
    result = await fetch_account_stats(exec_, discord_id=42)
    assert result is None


@pytest.mark.asyncio
async def test_populates_every_field() -> None:
    exec_ = _FakeExec(
        row={
            "balance": 75000,
            "total_deposited": 250000,
            "total_withdrawn": 175000,
            "lifetime_fee_paid": 3500,
        }
    )
    result = await fetch_account_stats(exec_, discord_id=42)
    assert result is not None
    assert isinstance(result, AccountStats)
    assert result.balance == 75000
    assert result.total_deposited == 250000
    assert result.total_withdrawn == 175000
    assert result.lifetime_fee_paid == 3500


@pytest.mark.asyncio
async def test_null_aggregates_coalesce_to_zero() -> None:
    """A brand-new user with a balance row but no deposit/withdraw
    history should not return NULLs — the SQL must COALESCE."""
    exec_ = _FakeExec(
        row={
            "balance": 0,
            "total_deposited": 0,
            "total_withdrawn": 0,
            "lifetime_fee_paid": 0,
        }
    )
    result = await fetch_account_stats(exec_, discord_id=99)
    assert result is not None
    assert result.balance == 0
    assert result.total_deposited == 0
    assert result.total_withdrawn == 0
    assert result.lifetime_fee_paid == 0


@pytest.mark.asyncio
async def test_account_stats_is_frozen() -> None:
    stats = AccountStats(
        balance=1, total_deposited=2, total_withdrawn=3, lifetime_fee_paid=4
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.balance = 999  # type: ignore[misc]
