"""Unit tests for `goldrush_core.discord_helpers.channel_binding`.

The helper resolves a channel id from ``dw.global_config`` for a
canonical key (``deposit``, ``withdraw``, ``cashier_alerts``, etc).
Used by the ``@require_channel`` decorator and by the cashier-alert
poster (Story 5.3).
"""

from __future__ import annotations

from typing import Any

import pytest
from goldrush_core.discord_helpers.channel_binding import resolve_channel_id


class _FakePool:
    def __init__(self, mapping: dict[str, int]) -> None:
        self._mapping = mapping

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> dict[str, Any] | None:
        key = args[0]
        if key in self._mapping:
            return {"value_int": self._mapping[key]}
        return None


@pytest.mark.asyncio
async def test_resolves_known_channel() -> None:
    pool = _FakePool({"channel_id_deposit": 100, "channel_id_withdraw": 200})
    assert await resolve_channel_id(pool, "deposit") == 100
    assert await resolve_channel_id(pool, "withdraw") == 200


@pytest.mark.asyncio
async def test_returns_none_when_unconfigured() -> None:
    pool = _FakePool({})
    assert await resolve_channel_id(pool, "deposit") is None


@pytest.mark.asyncio
async def test_unknown_canonical_key_raises() -> None:
    """Defensive: a typo in the key (`depsoit`) must surface as a
    ``ValueError`` at call time, not a silent ``None`` lookup."""
    pool = _FakePool({})
    with pytest.raises(ValueError):
        await resolve_channel_id(pool, "depsoit")
