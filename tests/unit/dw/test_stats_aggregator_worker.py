"""Unit tests for the stats aggregator worker (Story 8.5).

Story 8.5 says: every 15 min, recompute
``dw.cashier_stats.avg_claim_to_confirm_s`` (moving average over the
last 100 confirmations) and ``total_online_seconds`` (SUM of
``cashier_sessions.duration_s``) for every cashier with a stats row.

The worker is plain SQL — we don't add a SECURITY DEFINER fn because
the bot's role already has SELECT on tickets / sessions and UPDATE
on ``cashier_stats`` (see the GRANT in migration 0004).
"""

from __future__ import annotations

from typing import Any

import pytest

from deathroll_deposit_withdraw.workers.stats_aggregator import tick


class _FakePool:
    def __init__(self, *, cashiers: list[int] | None = None) -> None:
        self._cashiers = cashiers or []
        self.fetch_queries: list[str] = []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_queries.append(query)
        if "cashier_stats" in query:
            return [{"discord_id": cid} for cid in self._cashiers]
        return []

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        return "OK"


@pytest.mark.asyncio
async def test_tick_no_cashier_stats_rows_does_nothing() -> None:
    pool = _FakePool()
    updated = await tick(pool=pool)  # type: ignore[arg-type]
    assert updated == 0
    assert pool.executes == []


@pytest.mark.asyncio
async def test_tick_runs_one_update_per_cashier() -> None:
    pool = _FakePool(cashiers=[111, 222, 333])
    updated = await tick(pool=pool)  # type: ignore[arg-type]
    assert updated == 3
    assert len(pool.executes) == 3
    # Each UPDATE targets the cashier's row by discord_id.
    targeted = sorted(args[0] for _q, args in pool.executes)
    assert targeted == [111, 222, 333]


@pytest.mark.asyncio
async def test_tick_update_writes_avg_and_total_online() -> None:
    """The UPDATE statement covers both fields in one round-trip."""
    pool = _FakePool(cashiers=[111])
    await tick(pool=pool)  # type: ignore[arg-type]
    q, _args = pool.executes[0]
    assert "avg_claim_to_confirm_s" in q
    assert "total_online_seconds" in q
    # Both ticket families contribute confirmations to the avg.
    assert "deposit_tickets" in q
    assert "withdraw_tickets" in q
    # Online seconds come from sessions.
    assert "cashier_sessions" in q
