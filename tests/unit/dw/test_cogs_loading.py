"""Unit tests for the cog manifest and per-guild sync (Story 4.2).

The bot loads exactly six cogs at startup (one per spec §5.1 command
family). The tests verify:

- Every entry in ``EXTENSIONS`` is importable and exposes an async
  ``setup(bot)`` function (the discord.py extension contract).
- ``setup_hook`` actually loads all six cogs onto the bot.
- ``on_ready`` is async and routes through ``bot.tree.sync`` for the
  configured guild (we test the routing — Discord-side gateway
  events are integration-tested in Epic 14).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

import discord
import pytest
from deathroll_core.config import DwSettings
from deathroll_deposit_withdraw.client import EXTENSIONS, DwBot, build_bot
from pydantic import SecretStr


def _settings() -> DwSettings:
    return DwSettings(
        discord_token=SecretStr("dummy.token"),
        guild_id=1234567890,
        postgres_dsn=SecretStr("postgresql://x@y/z"),
        log_level="INFO",
        log_format="json",
    )


# ---------------------------------------------------------------------------
# EXTENSIONS contract
# ---------------------------------------------------------------------------


def test_extensions_lists_six_canonical_cogs() -> None:
    """Spec §5.1 has six command families: account, admin, cashier,
    deposit, ticket, withdraw. Each maps to one cog."""
    expected = {
        "deathroll_deposit_withdraw.cogs.account",
        "deathroll_deposit_withdraw.cogs.admin",
        "deathroll_deposit_withdraw.cogs.cashier",
        "deathroll_deposit_withdraw.cogs.deposit",
        "deathroll_deposit_withdraw.cogs.ticket",
        "deathroll_deposit_withdraw.cogs.withdraw",
    }
    assert set(EXTENSIONS) == expected


@pytest.mark.parametrize("ext", EXTENSIONS)
def test_each_extension_exposes_async_setup(ext: str) -> None:
    """discord.py expects ``async def setup(bot)`` in each extension
    module. A missing or sync ``setup`` causes ``load_extension`` to
    fail at runtime — we'd rather find that here."""
    module = importlib.import_module(ext)
    assert hasattr(module, "setup"), f"{ext} missing setup()"
    assert inspect.iscoroutinefunction(module.setup)


# ---------------------------------------------------------------------------
# setup_hook actually loads everything
# ---------------------------------------------------------------------------


def test_setup_hook_loads_all_six_cogs() -> None:
    """After ``setup_hook`` runs, ``bot.cogs`` should contain six
    entries — one per cog skeleton. Real commands are added by
    Stories 4.3 / 5 / 6 / 7 / 9 / 10."""

    class _FakePool:
        async def close(self) -> None:
            pass

    async def _factory(*, dsn: str, **kwargs: Any) -> _FakePool:
        return _FakePool()

    bot = build_bot(_settings(), pool_factory=_factory)

    async def _exercise() -> int:
        await bot.setup_hook()
        return len(bot.cogs)

    cog_count = asyncio.run(_exercise())
    assert cog_count == 6


# ---------------------------------------------------------------------------
# on_ready
# ---------------------------------------------------------------------------


def test_on_ready_is_async() -> None:
    assert inspect.iscoroutinefunction(DwBot.on_ready)


def test_on_ready_syncs_per_guild_object() -> None:
    """``on_ready`` syncs the command tree to the configured guild
    only — global sync is reserved for multi-server expansion. The
    test patches ``bot.tree.sync`` so we observe the call without
    talking to Discord."""

    class _FakePool:
        async def close(self) -> None:
            pass

    async def _factory(*, dsn: str, **kwargs: Any) -> _FakePool:
        return _FakePool()

    bot = build_bot(_settings(), pool_factory=_factory)
    sync_calls: list[discord.abc.Snowflake | None] = []

    async def _fake_sync(
        *, guild: discord.abc.Snowflake | None = None
    ) -> list[Any]:
        sync_calls.append(guild)
        # Simulate two synced commands so we can assert the count is logged
        return [object(), object()]

    bot.tree.sync = _fake_sync  # type: ignore[method-assign]

    async def _exercise() -> None:
        await bot.setup_hook()
        await bot.on_ready()

    asyncio.run(_exercise())

    assert len(sync_calls) == 1
    target = sync_calls[0]
    assert target is not None
    assert isinstance(target, discord.Object)
    assert target.id == 1234567890
