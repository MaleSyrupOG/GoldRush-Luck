"""Cog-level tests for the account cog (Story 4.3).

The cog registers two slash commands: ``/balance`` and ``/help``.
The handlers are exercised through the discord.py command tree, but
the test surface here is structural: each command must be registered
with the right name + description + parameter shape so the per-guild
sync in ``on_ready`` produces a deterministic command tree across
boots.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands
from deathroll_deposit_withdraw.cogs.account import AccountCog


def _build_bot() -> commands.Bot:
    """Construct a minimal commands.Bot for cog wiring tests."""
    return commands.Bot(
        command_prefix="!unused",
        intents=discord.Intents.default(),
    )


def test_account_cog_registers_balance_and_help_commands() -> None:
    """The cog must expose exactly ``/balance`` and ``/help`` as
    application commands so the sync logs the right count at boot.
    """
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AccountCog(bot))
        cog = bot.get_cog("AccountCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert names == {"balance", "help"}


def test_help_command_has_optional_topic_parameter() -> None:
    """``/help`` accepts an optional ``topic`` so users can deep-link
    into a single topic; without it the command lists all topics."""
    bot = _build_bot()

    async def _exercise() -> discord.app_commands.Command[Any, ..., Any] | None:
        await bot.add_cog(AccountCog(bot))
        cog = bot.get_cog("AccountCog")
        assert cog is not None
        for cmd in cog.get_app_commands():
            if cmd.name == "help":
                return cmd
        return None

    cmd = asyncio.run(_exercise())  # type: ignore[arg-type]
    assert cmd is not None
    # Discord app_commands surface parameters via .parameters; the
    # ``topic`` parameter must exist and be optional (required=False).
    topic_param = next((p for p in cmd.parameters if p.name == "topic"), None)
    assert topic_param is not None
    assert topic_param.required is False
