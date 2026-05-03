"""Unit tests for the ``list_audit_events`` wrapper (Story 10.8).

The bot's ``deathroll_dw`` role does not have SELECT on
``core.audit_log`` (deliberate — read access stays gated). Story 10.8
exposes a SECURITY DEFINER fn ``core.list_audit_events(p_target_id,
p_limit)`` and the wrapper just calls fetch over it.
"""

from __future__ import annotations

from typing import Any

import pytest
from deathroll_core.balance.dw_manager import list_audit_events


class _Pool:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append((query, args))
        return self._rows


@pytest.mark.asyncio
async def test_list_audit_events_passes_through_target_and_limit() -> None:
    pool = _Pool(rows=[{"id": 1}])
    result = await list_audit_events(
        pool,  # type: ignore[arg-type]
        target_id=222,
        limit=50,
    )
    assert result == [{"id": 1}]
    q, args = pool.queries[0]
    assert "core.list_audit_events" in q
    assert args == (222, 50)


@pytest.mark.asyncio
async def test_list_audit_events_target_id_none_means_all() -> None:
    pool = _Pool(rows=[])
    await list_audit_events(
        pool,  # type: ignore[arg-type]
        target_id=None,
        limit=25,
    )
    _q, args = pool.queries[0]
    # NULL target reaches Postgres as None — the SDF treats it as "all".
    assert args == (None, 25)
