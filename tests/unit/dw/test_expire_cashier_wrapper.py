"""Unit tests for the dw.expire_cashier wrapper (Story 8.3).

Migration ``20260503_0016_dw_expire_cashier`` introduces
``dw.expire_cashier(p_discord_id)`` — closes the cashier_sessions row
with ``end_reason='expired'`` and flips cashier_status to offline.
The sentinel ``cashier_not_online`` is translated to a new typed
exception ``CashierNotOnline``.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from deathroll_core.balance.dw_manager import expire_cashier
from deathroll_core.balance.exceptions import CashierNotOnline


class _Pool:
    def __init__(self, *, raise_message: str | None = None) -> None:
        self._raise = raise_message
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        self.queries.append((query, args))
        if self._raise is not None:
            raise asyncpg.RaiseError(self._raise)
        return "OK"


@pytest.mark.asyncio
async def test_expire_cashier_passes_discord_id_through() -> None:
    pool = _Pool()
    await expire_cashier(pool, discord_id=111)  # type: ignore[arg-type]
    q, args = pool.queries[0]
    assert "dw.expire_cashier" in q
    assert args == (111,)


@pytest.mark.asyncio
async def test_expire_cashier_translates_cashier_not_online() -> None:
    pool = _Pool(raise_message="cashier_not_online")
    with pytest.raises(CashierNotOnline):
        await expire_cashier(pool, discord_id=111)  # type: ignore[arg-type]
