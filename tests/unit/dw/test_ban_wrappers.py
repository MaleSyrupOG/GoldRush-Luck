"""Unit tests for the blacklist SECURITY DEFINER wrappers (Story 9.3).

Wrappers under test live in ``deathroll_core.balance.dw_manager``. They
talk to the SECURITY DEFINER fns from migration
``20260501_0012_dw_ban_fns`` and translate raised sentinels into the
typed exceptions in ``deathroll_core.balance.exceptions``.

The fake-pool pattern matches the rest of the wrapper test suite so
new wrappers follow a single, easy-to-grep template.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from deathroll_core.balance.dw_manager import ban_user, unban_user
from deathroll_core.balance.exceptions import (
    CannotBanTreasury,
    UserNotRegistered,
)


class _Pool:
    def __init__(
        self,
        *,
        raise_message: str | None = None,
    ) -> None:
        self._raise = raise_message
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> str:
        self.queries.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return "OK"


@pytest.mark.asyncio
async def test_ban_user_returns_silently_and_passes_args() -> None:
    pool = _Pool()
    await ban_user(
        pool,  # type: ignore[arg-type]
        user_id=123,
        reason="multiple chargebacks",
        admin_id=1,
    )
    q, args = pool.queries[0]
    assert "dw.ban_user" in q
    assert args == (123, "multiple chargebacks", 1)


@pytest.mark.asyncio
async def test_ban_user_translates_cannot_ban_treasury() -> None:
    pool = _Pool(raise_message="cannot_ban_treasury")
    with pytest.raises(CannotBanTreasury):
        await ban_user(
            pool,  # type: ignore[arg-type]
            user_id=0,
            reason="x",
            admin_id=1,
        )


@pytest.mark.asyncio
async def test_unban_user_returns_silently() -> None:
    pool = _Pool()
    await unban_user(
        pool,  # type: ignore[arg-type]
        user_id=123,
        admin_id=1,
    )
    q, args = pool.queries[0]
    assert "dw.unban_user" in q
    assert args == (123, 1)


@pytest.mark.asyncio
async def test_unban_user_translates_user_not_registered() -> None:
    pool = _Pool(raise_message="user_not_registered")
    with pytest.raises(UserNotRegistered):
        await unban_user(
            pool,  # type: ignore[arg-type]
            user_id=999,
            admin_id=1,
        )
