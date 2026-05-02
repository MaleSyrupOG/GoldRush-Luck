"""Unit tests for the dispute SECURITY DEFINER wrappers (Story 9.1).

The wrappers in ``dw_manager`` translate Postgres ``RaiseError``
sentinels into the typed exceptions in
``goldrush_core.balance.exceptions``. These tests cover the three
dispute paths: ``open_dispute``, ``resolve_dispute``, and the new
``reject_dispute`` (Story 9.1 SQL fn introduced in migration
``20260503_0013_dw_dispute_reject_fn``).

Identical fake-pool pattern as ``test_cashier_wrappers.py``: nothing
here talks to a real database — we drive the wrapper, capture the
query string, and verify error translation.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from goldrush_core.balance.dw_manager import (
    open_dispute,
    reject_dispute,
    resolve_dispute,
)
from goldrush_core.balance.exceptions import (
    DisputeAlreadyTerminal,
    DisputeNotFound,
    InvalidAction,
    InvalidOpenerRole,
    InvalidTicketType,
    PartialRefundRequiresPositiveAmount,
    RefundFullOnlyForWithdrawDisputes,
    TicketNotFound,
)


class _Pool:
    """Minimal pool returning a fixed value or raising a sentinel."""

    def __init__(
        self,
        *,
        return_value: int | None = None,
        raise_message: str | None = None,
    ) -> None:
        self._return = return_value
        self._raise = raise_message
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> int:
        self.queries.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return self._return if self._return is not None else 0

    async def execute(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> str:
        self.queries.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return "OK"


# ---------------------------------------------------------------------------
# open_dispute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_dispute_returns_new_id() -> None:
    pool = _Pool(return_value=42)
    rid = await open_dispute(
        pool,  # type: ignore[arg-type]
        ticket_type="deposit",
        ticket_uid="deposit-1",
        opener_id=111,
        opener_role="admin",
        reason="user reports cashier confirmed nothing",
    )
    assert rid == 42
    # Sanity: query routes to dw.open_dispute and carries the args in order.
    q, args = pool.queries[0]
    assert "dw.open_dispute" in q
    assert args == ("deposit", "deposit-1", 111, "admin", "user reports cashier confirmed nothing")


@pytest.mark.asyncio
async def test_open_dispute_translates_invalid_ticket_type() -> None:
    pool = _Pool(raise_message="invalid_ticket_type")
    with pytest.raises(InvalidTicketType):
        await open_dispute(
            pool,  # type: ignore[arg-type]
            ticket_type="deposit",
            ticket_uid="deposit-1",
            opener_id=111,
            opener_role="admin",
            reason="x",
        )


@pytest.mark.asyncio
async def test_open_dispute_translates_invalid_opener_role() -> None:
    pool = _Pool(raise_message="invalid_opener_role")
    with pytest.raises(InvalidOpenerRole):
        await open_dispute(
            pool,  # type: ignore[arg-type]
            ticket_type="deposit",
            ticket_uid="deposit-1",
            opener_id=111,
            opener_role="admin",
            reason="x",
        )


@pytest.mark.asyncio
async def test_open_dispute_translates_ticket_not_found() -> None:
    pool = _Pool(raise_message="ticket_not_found")
    with pytest.raises(TicketNotFound):
        await open_dispute(
            pool,  # type: ignore[arg-type]
            ticket_type="deposit",
            ticket_uid="deposit-999",
            opener_id=111,
            opener_role="admin",
            reason="x",
        )


# ---------------------------------------------------------------------------
# resolve_dispute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_dispute_no_action_succeeds() -> None:
    pool = _Pool()
    await resolve_dispute(
        pool,  # type: ignore[arg-type]
        dispute_id=42,
        action="no-action",
        amount=None,
        resolved_by=1,
    )
    q, args = pool.queries[0]
    assert "dw.resolve_dispute" in q
    # NULL amount must reach Postgres as None, not 0.
    assert args == (42, "no-action", None, 1)


@pytest.mark.asyncio
async def test_resolve_dispute_partial_refund_passes_amount() -> None:
    pool = _Pool()
    await resolve_dispute(
        pool,  # type: ignore[arg-type]
        dispute_id=42,
        action="partial-refund",
        amount=50_000,
        resolved_by=1,
    )
    _q, args = pool.queries[0]
    assert args == (42, "partial-refund", 50_000, 1)


@pytest.mark.asyncio
async def test_resolve_dispute_translates_dispute_not_found() -> None:
    pool = _Pool(raise_message="dispute_not_found")
    with pytest.raises(DisputeNotFound):
        await resolve_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=999,
            action="no-action",
            amount=None,
            resolved_by=1,
        )


@pytest.mark.asyncio
async def test_resolve_dispute_translates_already_terminal() -> None:
    pool = _Pool(raise_message="dispute_already_terminal (resolved)")
    with pytest.raises(DisputeAlreadyTerminal):
        await resolve_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=42,
            action="no-action",
            amount=None,
            resolved_by=1,
        )


@pytest.mark.asyncio
async def test_resolve_dispute_translates_partial_refund_no_amount() -> None:
    pool = _Pool(raise_message="partial_refund_requires_positive_amount")
    with pytest.raises(PartialRefundRequiresPositiveAmount):
        await resolve_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=42,
            action="partial-refund",
            amount=0,
            resolved_by=1,
        )


@pytest.mark.asyncio
async def test_resolve_dispute_translates_refund_full_only_withdraw() -> None:
    pool = _Pool(raise_message="refund_full_only_for_withdraw_disputes")
    with pytest.raises(RefundFullOnlyForWithdrawDisputes):
        await resolve_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=42,
            action="refund-full",
            amount=None,
            resolved_by=1,
        )


@pytest.mark.asyncio
async def test_resolve_dispute_translates_invalid_action() -> None:
    pool = _Pool(raise_message="invalid_action (gibberish)")
    with pytest.raises(InvalidAction):
        await resolve_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=42,
            action="no-action",  # message is what the SQL fn actually returned
            amount=None,
            resolved_by=1,
        )


# ---------------------------------------------------------------------------
# reject_dispute (Story 9.1 — new SQL fn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_dispute_succeeds_silently() -> None:
    pool = _Pool()
    await reject_dispute(
        pool,  # type: ignore[arg-type]
        dispute_id=42,
        reason="evidence does not support claim",
        admin_id=1,
    )
    q, args = pool.queries[0]
    assert "dw.reject_dispute" in q
    assert args == (42, "evidence does not support claim", 1)


@pytest.mark.asyncio
async def test_reject_dispute_translates_dispute_not_found() -> None:
    pool = _Pool(raise_message="dispute_not_found")
    with pytest.raises(DisputeNotFound):
        await reject_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=999,
            reason="x",
            admin_id=1,
        )


@pytest.mark.asyncio
async def test_reject_dispute_translates_already_terminal() -> None:
    pool = _Pool(raise_message="dispute_already_terminal (rejected)")
    with pytest.raises(DisputeAlreadyTerminal):
        await reject_dispute(
            pool,  # type: ignore[arg-type]
            dispute_id=42,
            reason="x",
            admin_id=1,
        )
