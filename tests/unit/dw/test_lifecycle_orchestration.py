"""Unit tests for the lifecycle orchestration helpers.

Covers ``claim_ticket_for_cashier``, ``release_ticket_by_cashier``
and ``cancel_ticket_dispatch``. Each helper wraps a SECURITY DEFINER
call and translates Postgres exceptions into typed
``LifecycleOutcome.*`` variants.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from goldrush_deposit_withdraw.tickets.orchestration import (
    LifecycleOutcome,
    cancel_ticket_dispatch,
    claim_ticket_for_cashier,
    release_ticket_by_cashier,
)


class _Pool:
    """Minimal pool used for both ``execute`` (claim/release/cancel_deposit)
    and ``fetchval`` (cancel_withdraw)."""

    def __init__(self, raise_message: str | None = None) -> None:
        self._raise = raise_message
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        self.calls.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return "OK"

    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> int:
        self.calls.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        # cancel_withdraw returns the new balance; arbitrary positive int.
        return 0


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_returns_success_on_ok() -> None:
    pool = _Pool()
    outcome = await claim_ticket_for_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.Success)


@pytest.mark.asyncio
async def test_claim_translates_already_claimed() -> None:
    pool = _Pool(raise_message="already_claimed (status=claimed)")
    outcome = await claim_ticket_for_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.AlreadyClaimed)


@pytest.mark.asyncio
async def test_claim_translates_region_mismatch() -> None:
    pool = _Pool(raise_message="region_mismatch (cashier 42 has no active char in region NA)")
    outcome = await claim_ticket_for_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.RegionMismatch)


@pytest.mark.asyncio
async def test_claim_translates_ticket_not_found() -> None:
    pool = _Pool(raise_message="ticket_not_found")
    outcome = await claim_ticket_for_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="bogus",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.TicketNotFound)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_returns_success() -> None:
    pool = _Pool()
    outcome = await release_ticket_by_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.Success)


@pytest.mark.asyncio
async def test_release_translates_wrong_cashier() -> None:
    """Spec §4.1: only the claimer can release."""
    pool = _Pool(raise_message="wrong_cashier (claimed_by=999 calling=42)")
    outcome = await release_ticket_by_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.WrongCashier)


@pytest.mark.asyncio
async def test_release_translates_not_claimed() -> None:
    pool = _Pool(raise_message="ticket_not_claimed (status=open)")
    outcome = await release_ticket_by_cashier(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        cashier_id=42,
    )
    assert isinstance(outcome, LifecycleOutcome.NotClaimed)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_dispatches_to_deposit_fn() -> None:
    pool = _Pool()
    outcome = await cancel_ticket_dispatch(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        actor_id=42,
        reason="user requested",
    )
    assert isinstance(outcome, LifecycleOutcome.Success)
    # The query string includes the deposit cancel fn name.
    assert any("cancel_deposit" in q for q, _ in pool.calls)


@pytest.mark.asyncio
async def test_cancel_dispatches_to_withdraw_fn() -> None:
    """Withdraw cancel ALSO refunds the locked balance (in the SQL fn)."""
    pool = _Pool()
    outcome = await cancel_ticket_dispatch(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="withdraw",
        ticket_uid="withdraw-1",
        actor_id=42,
        reason="cashier unavailable",
    )
    assert isinstance(outcome, LifecycleOutcome.Success)
    assert any("cancel_withdraw" in q for q, _ in pool.calls)


@pytest.mark.asyncio
async def test_cancel_translates_already_terminal() -> None:
    pool = _Pool(raise_message="ticket_already_terminal (status=confirmed)")
    outcome = await cancel_ticket_dispatch(
        pool=pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        actor_id=42,
        reason="user requested",
    )
    assert isinstance(outcome, LifecycleOutcome.AlreadyTerminal)
