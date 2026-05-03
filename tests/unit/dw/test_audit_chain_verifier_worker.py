"""Unit tests for the audit chain verifier (Story 8.6).

Story 8.6 says: every 6 h (or on demand via ``/admin-verify-audit``),
walk ``core.audit_log`` from ``last_verified_audit_row_id`` (stored
in ``dw.global_config``) recomputing the HMAC chain. On break, emit
a critical-level structlog event so the alerting layer (Loki +
Alertmanager — Story 11.x) escalates. Persist the new last verified
id back to ``dw.global_config`` so the next iteration picks up where
this one left off.

The chain recomputation itself lives in
``core.verify_audit_chain(p_from_id, p_max_rows)`` (SECURITY DEFINER
since the bot's role doesn't have SELECT on ``core.audit_log``);
the worker is a thin loop on top.
"""

from __future__ import annotations

from typing import Any

import pytest
from goldrush_deposit_withdraw.workers.audit_chain_verifier import tick


class _FakePool:
    def __init__(
        self,
        *,
        last_verified_id: int = 0,
        verify_result: dict[str, Any] | None = None,
    ) -> None:
        self._last_verified_id = last_verified_id
        # ``checked_count``, ``last_verified_id``, ``broken_at_id``.
        self._verify_result = verify_result or {
            "checked_count": 0,
            "last_verified_id": 0,
            "broken_at_id": None,
        }
        self.fetchrow_queries: list[tuple[str, tuple[Any, ...]]] = []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.fetchrow_queries.append((query, args))
        if "global_config" in query and args and args[0] == "last_verified_audit_row_id":
            return {"value_int": self._last_verified_id}
        if "core.verify_audit_chain" in query:
            return self._verify_result
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        return "OK"


@pytest.mark.asyncio
async def test_tick_walks_chain_from_persisted_id() -> None:
    """``tick`` reads the persisted ``last_verified_audit_row_id`` from
    ``dw.global_config`` and passes it to the verifier as the
    starting point."""
    pool = _FakePool(
        last_verified_id=42,
        verify_result={
            "checked_count": 5,
            "last_verified_id": 47,
            "broken_at_id": None,
        },
    )

    result = await tick(pool=pool)  # type: ignore[arg-type]
    assert result.broken_at_id is None
    assert result.last_verified_id == 47

    # The verify call passed p_from_id = 42 (the persisted id + 1
    # would be wrong; we want to re-verify the boundary).
    verify_calls = [
        args
        for q, args in pool.fetchrow_queries
        if "core.verify_audit_chain" in q
    ]
    assert len(verify_calls) == 1
    assert verify_calls[0][0] == 42


@pytest.mark.asyncio
async def test_tick_persists_new_last_verified_id() -> None:
    pool = _FakePool(
        last_verified_id=0,
        verify_result={
            "checked_count": 100,
            "last_verified_id": 100,
            "broken_at_id": None,
        },
    )
    await tick(pool=pool)  # type: ignore[arg-type]

    upserts = [
        args
        for q, args in pool.executes
        if "global_config" in q and "last_verified_audit_row_id" in args
    ]
    assert len(upserts) == 1
    # The persisted value matches the verifier's last_verified_id.
    assert 100 in upserts[0]


@pytest.mark.asyncio
async def test_tick_does_not_persist_when_chain_unchanged() -> None:
    """If checked_count==0 the chain hasn't grown — skip the UPSERT to
    avoid pointless writes."""
    pool = _FakePool(
        last_verified_id=42,
        verify_result={
            "checked_count": 0,
            "last_verified_id": 42,
            "broken_at_id": None,
        },
    )
    await tick(pool=pool)  # type: ignore[arg-type]
    upserts = [
        args
        for q, args in pool.executes
        if "global_config" in q and "last_verified_audit_row_id" in args
    ]
    assert upserts == []


@pytest.mark.asyncio
async def test_tick_returns_broken_at_when_chain_breaks() -> None:
    """When ``broken_at_id`` is non-null the worker DOES NOT advance the
    last_verified_id — admins need the chain pointer left at the last
    known-good row so a re-run after the fix continues from there."""
    pool = _FakePool(
        last_verified_id=10,
        verify_result={
            "checked_count": 5,
            "last_verified_id": 14,
            "broken_at_id": 15,
        },
    )

    result = await tick(pool=pool)  # type: ignore[arg-type]
    assert result.broken_at_id == 15
    # No persist on break — the next iteration will retry the same range.
    upserts = [
        args
        for q, args in pool.executes
        if "global_config" in q and "last_verified_audit_row_id" in args
    ]
    assert upserts == []


@pytest.mark.asyncio
async def test_tick_starts_from_zero_when_no_persisted_id() -> None:
    """Empty config → start verifying from id=0 (the first row)."""

    class _NoConfigPool(_FakePool):
        async def fetchrow(self, query: str, *args: Any) -> Any:
            self.fetchrow_queries.append((query, args))
            if "global_config" in query:
                return None  # no row yet
            if "core.verify_audit_chain" in query:
                return self._verify_result
            return None

    pool = _NoConfigPool(
        last_verified_id=0,
        verify_result={
            "checked_count": 3,
            "last_verified_id": 3,
            "broken_at_id": None,
        },
    )
    result = await tick(pool=pool)  # type: ignore[arg-type]
    assert result.last_verified_id == 3
    verify_calls = [
        args
        for q, args in pool.fetchrow_queries
        if "core.verify_audit_chain" in q
    ]
    assert verify_calls[0][0] == 0
