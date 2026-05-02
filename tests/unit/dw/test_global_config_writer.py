"""Unit tests for `goldrush_deposit_withdraw.setup.global_config_writer`.

The writer persists the channel id map produced by
``setup_or_reuse_channels`` into ``dw.global_config`` rows keyed
``channel_id_<key>``. Idempotent: re-running with the same map
just updates the rows in place.
"""

from __future__ import annotations

import asyncio
from typing import Any

from goldrush_deposit_withdraw.setup.global_config_writer import (
    persist_channel_ids,
)


class _FakePool:
    """Records every UPSERT for assertion."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, int, int]] = []

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        # We capture (key, value, actor) regardless of query shape so the
        # test reads the persisted state, not the SQL.
        self.upserts.append((args[0], int(args[1]), int(args[2])))
        return "OK"


def test_persist_writes_one_row_per_channel() -> None:
    pool = _FakePool()
    asyncio.run(
        persist_channel_ids(
            pool,  # type: ignore[arg-type]
            channel_id_map={
                "deposit": 100,
                "withdraw": 200,
                "online_cashiers": 300,
            },
            actor_id=42,
        )
    )
    keys = {k for k, _, _ in pool.upserts}
    assert keys == {
        "channel_id_deposit",
        "channel_id_withdraw",
        "channel_id_online_cashiers",
    }
    # actor_id propagates so the audit / global_config row records who ran setup.
    assert {a for _, _, a in pool.upserts} == {42}


def test_persist_is_idempotent_in_call_count() -> None:
    """The writer issues one UPSERT per key — same on first run and re-run."""
    pool = _FakePool()
    keymap = {"deposit": 100, "withdraw": 200}

    async def _exercise() -> None:
        await persist_channel_ids(pool, channel_id_map=keymap, actor_id=42)  # type: ignore[arg-type]
        await persist_channel_ids(pool, channel_id_map=keymap, actor_id=42)  # type: ignore[arg-type]

    asyncio.run(_exercise())
    # 2 keys * 2 calls = 4 upserts; the SQL-level upsert is idempotent
    # so the two calls converge to the same persisted state.
    assert len(pool.upserts) == 4


def test_persist_handles_empty_map_gracefully() -> None:
    """Dry-run might produce an empty map; the writer must not crash."""
    pool = _FakePool()
    asyncio.run(
        persist_channel_ids(
            pool,  # type: ignore[arg-type]
            channel_id_map={},
            actor_id=42,
        )
    )
    assert pool.upserts == []
