"""Unit tests for the cashier-system SECURITY DEFINER wrappers.

The wrappers in ``dw_manager`` translate Postgres ``RaiseError``
sentinels into the typed exceptions in
``goldrush_core.balance.exceptions``. These tests cover the
cashier-specific paths added for Epic 7.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from goldrush_core.balance.dw_manager import (
    add_cashier_character,
    remove_cashier_character,
    set_cashier_status,
)
from goldrush_core.balance.exceptions import (
    CharacterNotFoundOrAlreadyRemoved,
    InvalidRegion,
    InvalidStatus,
)


class _Pool:
    """Minimal pool returning a fixed integer or raising a sentinel."""

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

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        self.queries.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return "OK"


# ---------------------------------------------------------------------------
# add_cashier_character
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_cashier_character_returns_row_id() -> None:
    pool = _Pool(return_value=7)
    rid = await add_cashier_character(
        pool,  # type: ignore[arg-type]
        discord_id=42,
        char="Goldrush",
        realm="Stormrage",
        region="EU",
        faction="Horde",
    )
    assert rid == 7


@pytest.mark.asyncio
async def test_add_cashier_character_translates_invalid_region() -> None:
    pool = _Pool(raise_message="invalid_region (FR)")
    with pytest.raises(InvalidRegion):
        await add_cashier_character(
            pool,  # type: ignore[arg-type]
            discord_id=42,
            char="Goldrush",
            realm="Stormrage",
            region="EU",  # the message comes from the SQL fn — what we send is irrelevant
            faction="Horde",
        )


# ---------------------------------------------------------------------------
# remove_cashier_character
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_cashier_character_translates_not_found() -> None:
    pool = _Pool(raise_message="character_not_found_or_already_removed")
    with pytest.raises(CharacterNotFoundOrAlreadyRemoved):
        await remove_cashier_character(
            pool,  # type: ignore[arg-type]
            discord_id=42,
            char="Bogus",
            realm="Stormrage",
            region="EU",
        )


@pytest.mark.asyncio
async def test_remove_cashier_character_succeeds_silently() -> None:
    pool = _Pool()
    # No exception raised → success.
    await remove_cashier_character(
        pool,  # type: ignore[arg-type]
        discord_id=42,
        char="Goldrush",
        realm="Stormrage",
        region="EU",
    )


# ---------------------------------------------------------------------------
# set_cashier_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cashier_status_online_succeeds() -> None:
    pool = _Pool()
    await set_cashier_status(
        pool,  # type: ignore[arg-type]
        discord_id=42,
        status="online",
    )


@pytest.mark.asyncio
async def test_set_cashier_status_translates_invalid_status() -> None:
    pool = _Pool(raise_message="invalid_status (typo)")
    with pytest.raises(InvalidStatus):
        await set_cashier_status(
            pool,  # type: ignore[arg-type]
            discord_id=42,
            status="online",  # message is what the SQL fn returned
        )
