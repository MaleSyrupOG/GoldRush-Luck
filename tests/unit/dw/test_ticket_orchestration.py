"""Unit tests for the deposit / withdraw ticket orchestration helpers.

The orchestration layer wraps the SECURITY DEFINER calls in a typed
result so the cog handler can dispatch to the right embed without
``except`` plumbing in the command path.

These tests pass an in-process fake pool that raises
``asyncpg.RaiseError`` for the named sentinel; the orchestration
translates each into the matching ``Outcome.*`` variant.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from deathroll_core.models.dw_pydantic import DepositModalInput, WithdrawModalInput
from deathroll_deposit_withdraw.tickets.orchestration import (
    DepositOutcome,
    WithdrawOutcome,
    open_deposit_ticket,
    open_withdraw_ticket,
)
from pydantic import SecretStr  # noqa: F401  — re-export for symmetry with other tests


def _deposit_payload() -> DepositModalInput:
    return DepositModalInput(
        char_name="Malesyrup",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=50000,
    )


def _withdraw_payload() -> WithdrawModalInput:
    return WithdrawModalInput(
        char_name="Malesyrup",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=50000,
    )


class _FakePoolReturning:
    """Returns a fixed value from ``fetchval``."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> Any:
        return self._value


class _FakePoolRaising:
    """Raises an ``asyncpg.RaiseError`` with the given Postgres message."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> Any:
        # asyncpg.RaiseError is what dw_manager.translate_pg_error
        # expects to catch.
        raise asyncpg.RaiseError(self._message)


# ---------------------------------------------------------------------------
# Deposit orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_deposit_returns_success_on_uid() -> None:
    pool = _FakePoolReturning("deposit-1")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.Success)
    assert outcome.ticket_uid == "deposit-1"


@pytest.mark.asyncio
async def test_open_deposit_translates_user_banned() -> None:
    pool = _FakePoolRaising("user_banned")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.UserBanned)


@pytest.mark.asyncio
async def test_open_deposit_translates_amount_out_of_range() -> None:
    pool = _FakePoolRaising("amount_out_of_range (got 50, expected 200 to 200000)")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.AmountOutOfRange)
    assert outcome.message  # the message includes the bounds


@pytest.mark.asyncio
async def test_open_deposit_translates_global_config_missing() -> None:
    pool = _FakePoolRaising("global_config missing required deposit keys")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.ConfigError)


@pytest.mark.asyncio
async def test_open_deposit_translates_invalid_region() -> None:
    pool = _FakePoolRaising("invalid_region (FR)")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.InvalidInput)


@pytest.mark.asyncio
async def test_open_deposit_unknown_error_falls_back_to_unexpected() -> None:
    pool = _FakePoolRaising("something_we_did_not_plan_for: weird")
    outcome = await open_deposit_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_deposit_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, DepositOutcome.Unexpected)
    assert "weird" in outcome.message


# ---------------------------------------------------------------------------
# Withdraw orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_withdraw_returns_success_with_uid() -> None:
    pool = _FakePoolReturning("withdraw-1")
    outcome = await open_withdraw_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_withdraw_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, WithdrawOutcome.Success)
    assert outcome.ticket_uid == "withdraw-1"


@pytest.mark.asyncio
async def test_open_withdraw_translates_insufficient_balance() -> None:
    """Withdraw-specific: the SECURITY DEFINER fn raises
    ``insufficient_balance (have N, need M)`` when the balance row
    cannot cover the requested amount."""
    pool = _FakePoolRaising("insufficient_balance (have 100, need 50000)")
    outcome = await open_withdraw_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_withdraw_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, WithdrawOutcome.InsufficientBalance)
    assert "have" in outcome.message
    assert "need" in outcome.message


@pytest.mark.asyncio
async def test_open_withdraw_translates_user_not_registered() -> None:
    """A withdraw against a user who has never deposited yields
    ``user_not_registered``. Cog renders the no_balance redirect."""
    pool = _FakePoolRaising("user_not_registered")
    outcome = await open_withdraw_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_withdraw_payload(),
        discord_id=999,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, WithdrawOutcome.UserNotRegistered)


@pytest.mark.asyncio
async def test_open_withdraw_translates_user_banned() -> None:
    pool = _FakePoolRaising("user_banned")
    outcome = await open_withdraw_ticket(
        pool=pool,  # type: ignore[arg-type]
        payload=_withdraw_payload(),
        discord_id=42,
        thread_id=100,
        parent_channel_id=200,
    )
    assert isinstance(outcome, WithdrawOutcome.UserBanned)
