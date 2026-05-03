"""Tests for ``persist_config_int`` (Story 10.2 helper).

Story 10.2 wraps a few ``dw.global_config`` UPSERTs (deposit limits,
withdraw limits, withdraw fee) in slash commands. The shared writer
``persist_config_int`` lives in
``goldrush_deposit_withdraw.setup.global_config_writer`` so it can
be reused for any future int-typed config key.
"""

from __future__ import annotations

from typing import Any

import pytest
from goldrush_deposit_withdraw.setup.global_config_writer import persist_config_int


class _FakePool:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        return "OK"


@pytest.mark.asyncio
async def test_persist_config_int_upserts_with_actor() -> None:
    pool = _FakePool()
    await persist_config_int(
        pool,  # type: ignore[arg-type]
        key="min_deposit_g",
        value=500,
        actor_id=42,
    )
    assert len(pool.executes) == 1
    q, args = pool.executes[0]
    assert "global_config" in q
    assert "ON CONFLICT (key) DO UPDATE" in q
    assert args == ("min_deposit_g", 500, 42)


@pytest.mark.asyncio
async def test_persist_config_int_does_not_prefix_key() -> None:
    """Unlike ``persist_channel_ids`` (which prepends ``channel_id_``)
    or ``persist_role_ids`` (``role_id_``), the int writer takes the
    raw key — keys already have stable names like ``withdraw_fee_bps``
    that don't fit any prefix pattern."""
    pool = _FakePool()
    await persist_config_int(
        pool,  # type: ignore[arg-type]
        key="withdraw_fee_bps",
        value=200,
        actor_id=1,
    )
    _q, args = pool.executes[0]
    # The key stored must be exactly what callers passed.
    assert args[0] == "withdraw_fee_bps"
