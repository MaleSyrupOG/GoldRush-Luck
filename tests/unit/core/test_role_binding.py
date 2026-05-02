"""Unit tests for `goldrush_core.discord_helpers.role_binding`.

The helper resolves a role id from ``dw.global_config`` (set by
``/admin-setup``) so the bot can render real role mentions
``<@&id>`` instead of the literal ``@cashier`` string Discord
treats as plain text.
"""

from __future__ import annotations

from typing import Any

import pytest
from goldrush_core.discord_helpers.role_binding import (
    CANONICAL_ROLE_KEYS,
    resolve_role_id,
    role_mention,
)


class _FakePool:
    def __init__(self, mapping: dict[str, int]) -> None:
        self._mapping = mapping

    async def fetchrow(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> dict[str, Any] | None:
        key = args[0]
        if key in self._mapping:
            return {"value_int": self._mapping[key]}
        return None


def test_canonical_role_keys() -> None:
    """The canonical roles for v1: cashier and admin."""
    assert CANONICAL_ROLE_KEYS == frozenset({"cashier", "admin"})


@pytest.mark.asyncio
async def test_resolve_role_id_returns_value_when_set() -> None:
    pool = _FakePool({"role_id_cashier": 1234567890})
    assert await resolve_role_id(pool, "cashier") == 1234567890


@pytest.mark.asyncio
async def test_resolve_role_id_returns_none_when_unset() -> None:
    pool = _FakePool({})
    assert await resolve_role_id(pool, "cashier") is None


@pytest.mark.asyncio
async def test_resolve_role_id_unknown_key_raises() -> None:
    pool = _FakePool({})
    with pytest.raises(ValueError):
        await resolve_role_id(pool, "casheir")  # typo on purpose


@pytest.mark.asyncio
async def test_role_mention_returns_real_mention_when_role_bound() -> None:
    pool = _FakePool({"role_id_cashier": 999})
    rendered = await role_mention(pool, "cashier")
    assert rendered == "<@&999>"


@pytest.mark.asyncio
async def test_role_mention_falls_back_to_literal_when_unbound() -> None:
    """Pre-/admin-setup or with no cashier_role passed → graceful
    degradation to a literal ``@cashier`` string."""
    pool = _FakePool({})
    rendered = await role_mention(pool, "cashier")
    assert rendered == "@cashier"


@pytest.mark.asyncio
async def test_role_mention_works_for_admin_too() -> None:
    pool = _FakePool({"role_id_admin": 4242})
    rendered = await role_mention(pool, "admin")
    assert rendered == "<@&4242>"
