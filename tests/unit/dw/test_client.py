"""Unit tests for `deathroll_deposit_withdraw.client`.

The bot is a thin subclass of ``discord.ext.commands.Bot``. Real
command sync and Discord login are exercised in Epic 14 integration
tests; these unit tests guard the structural contract:

- ``DwBot`` is a subclass of ``commands.Bot``.
- ``EXTENSIONS`` is a tuple of strings (cog import paths).
- ``setup_hook`` is async and assigns ``self.pool`` from the supplied
  pool factory.
- ``build_bot(settings)`` returns a ``DwBot`` configured with the
  expected intents and a guild-scoped command tree.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import discord
from discord.ext import commands
from deathroll_core.config import DwSettings
from deathroll_deposit_withdraw.client import EXTENSIONS, DwBot, build_bot
from pydantic import SecretStr


def _settings() -> DwSettings:
    """Build a Settings instance with synthetic values; no env required."""
    return DwSettings(
        discord_token=SecretStr("dummy.token"),
        guild_id=1,
        postgres_dsn=SecretStr("postgresql://x@y/z"),
        log_level="INFO",
        log_format="json",
    )


def test_extensions_is_tuple_of_strings() -> None:
    assert isinstance(EXTENSIONS, tuple)
    for ext in EXTENSIONS:
        assert isinstance(ext, str)
        assert ext.startswith("deathroll_deposit_withdraw.")


def test_dw_bot_is_a_commands_bot_subclass() -> None:
    assert issubclass(DwBot, commands.Bot)


def test_dw_bot_setup_hook_is_async() -> None:
    """``setup_hook`` is the canonical place to open the DB pool;
    discord.py calls it as a coroutine before logging in."""
    assert inspect.iscoroutinefunction(DwBot.setup_hook)


def test_build_bot_returns_dw_bot() -> None:
    bot = build_bot(_settings())
    assert isinstance(bot, DwBot)


def test_build_bot_uses_default_intents_no_privileged() -> None:
    """Spec §6.6 says no privileged intents in v1.

    ``discord.Intents.default()`` already excludes ``members``,
    ``message_content`` and ``presences`` — we just need to confirm
    we did not opt-in to any of them."""
    bot = build_bot(_settings())
    assert bot.intents.members is False
    assert bot.intents.message_content is False
    assert bot.intents.presences is False


def test_setup_hook_assigns_pool_from_factory() -> None:
    """``setup_hook`` calls the pool factory once and stores the result
    on ``self.pool`` so cogs and commands can acquire connections.

    Loading cogs is part of Story 4.2, so an empty ``EXTENSIONS`` is
    expected at this layer — the test only asserts that the pool was
    set, not that any cog was loaded.
    """
    settings = _settings()
    factory_calls: list[dict[str, Any]] = []

    class _FakePool:
        closed = False

        async def close(self) -> None:
            self.closed = True

    async def _factory(*, dsn: str, **kwargs: Any) -> _FakePool:
        factory_calls.append({"dsn": dsn, "kwargs": kwargs})
        return _FakePool()

    bot = build_bot(settings, pool_factory=_factory)
    asyncio.run(bot.setup_hook())

    assert isinstance(bot.pool, _FakePool)
    assert len(factory_calls) == 1
    # The DSN is unwrapped from SecretStr before being passed to asyncpg.
    assert factory_calls[0]["dsn"] == settings.postgres_dsn.get_secret_value()


def test_close_closes_pool() -> None:
    """``DwBot.close`` must release the DB pool so the container can
    shut down without leaking sockets."""
    settings = _settings()

    class _FakePool:
        closed = False

        async def close(self) -> None:
            self.closed = True

    async def _factory(*, dsn: str, **kwargs: Any) -> _FakePool:
        return _FakePool()

    bot = build_bot(settings, pool_factory=_factory)

    async def _exercise() -> _FakePool:
        await bot.setup_hook()
        pool = bot.pool
        assert pool is not None
        await bot.close_pool()
        return pool

    pool = asyncio.run(_exercise())
    assert pool.closed is True


def test_guild_scoped_object_uses_guild_id() -> None:
    """The bot exposes ``settings.guild_id`` so cogs and on_ready can
    sync the command tree per-guild rather than globally."""
    bot = build_bot(_settings())
    assert bot.settings.guild_id == 1
    # Sanity: a discord.Object built from this id matches.
    target = discord.Object(id=bot.settings.guild_id)
    assert target.id == 1
