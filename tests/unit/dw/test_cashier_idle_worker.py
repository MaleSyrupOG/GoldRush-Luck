"""Unit tests for the cashier-idle worker (Story 8.3).

Story 8.3 says: every 5 min, every ``dw.cashier_status`` row with
``status='online' AND last_active_at < NOW() - 1 h`` is auto-set
offline; the open ``dw.cashier_sessions`` row is closed with
``end_reason='expired'``.

The new ``dw.expire_cashier`` SECURITY DEFINER fn (migration
``20260503_0016_dw_expire_cashier``) does the atomic transition;
the worker is a thin loop on top.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from goldrush_deposit_withdraw.workers.cashier_idle import tick


class _FakePool:
    def __init__(
        self,
        *,
        idle_cashiers: list[dict[str, Any]] | None = None,
        expire_raises: Exception | None = None,
    ) -> None:
        self._idle = idle_cashiers or []
        self._raises = expire_raises
        self.fetch_queries: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_queries.append(query)
        if "cashier_status" in query:
            return self._idle
        return []

    async def execute(self, query: str, *args: Any) -> str:
        if "dw.expire_cashier" in query:
            self.calls.append(("expire_cashier", args))
            if self._raises is not None:
                raise self._raises
        return "OK"


@pytest.mark.asyncio
async def test_tick_no_idle_cashiers_does_nothing() -> None:
    pool = _FakePool()
    expired = await tick(pool=pool)  # type: ignore[arg-type]
    assert expired == 0
    assert pool.calls == []


@pytest.mark.asyncio
async def test_tick_expires_each_idle_online_cashier() -> None:
    pool = _FakePool(
        idle_cashiers=[
            {"discord_id": 111},
            {"discord_id": 222},
        ]
    )
    expired = await tick(pool=pool)  # type: ignore[arg-type]
    assert expired == 2
    assert {c[1][0] for c in pool.calls} == {111, 222}


@pytest.mark.asyncio
async def test_tick_swallows_cashier_not_online_idempotency() -> None:
    """If a cashier set themselves offline between SELECT and the
    expire fn, the SDF raises ``cashier_not_online``. The worker
    swallows it — desired state."""
    pool = _FakePool(
        idle_cashiers=[{"discord_id": 111}],
        expire_raises=asyncpg.RaiseError("cashier_not_online"),
    )
    expired = await tick(pool=pool)  # type: ignore[arg-type]
    assert expired == 0
