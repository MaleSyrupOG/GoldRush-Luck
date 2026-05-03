"""Cog-registration tests for the deposit and withdraw flows.

The end-to-end Discord interaction is exercised in Epic 14
integration tests; this file guards the structural contract:

- The cogs register exactly one slash command each (``deposit`` /
  ``withdraw``) with the right name and description.
- The commands accept zero parameters at the slash level — input
  is captured via the modal that opens on click.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands
from deathroll_deposit_withdraw.cogs.deposit import DepositCog
from deathroll_deposit_withdraw.cogs.withdraw import WithdrawCog


def _build_bot() -> commands.Bot:
    return commands.Bot(
        command_prefix="!unused",
        intents=discord.Intents.default(),
    )


def test_deposit_cog_registers_deposit_slash_command() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(DepositCog(bot))
        cog = bot.get_cog("DepositCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert names == {"deposit"}


def test_withdraw_cog_registers_withdraw_slash_command() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(WithdrawCog(bot))
        cog = bot.get_cog("WithdrawCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert names == {"withdraw"}


def test_deposit_command_takes_no_user_parameters() -> None:
    """Input is captured via the modal that opens on invocation;
    the slash command itself takes no arguments."""
    bot = _build_bot()

    async def _exercise() -> int:
        await bot.add_cog(DepositCog(bot))
        cog = bot.get_cog("DepositCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "deposit")
        return len(cmd.parameters)

    assert asyncio.run(_exercise()) == 0


def test_withdraw_command_takes_no_user_parameters() -> None:
    bot = _build_bot()

    async def _exercise() -> int:
        await bot.add_cog(WithdrawCog(bot))
        cog = bot.get_cog("WithdrawCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "withdraw")
        return len(cmd.parameters)

    assert asyncio.run(_exercise()) == 0


# ---------------------------------------------------------------------------
# Story 9.3 — banned user surface: the deposit and withdraw cogs map
# the UserBanned outcome to a friendly "blacklisted" ephemeral. Drives
# the spec §6.4 user-facing copy.
# ---------------------------------------------------------------------------


def test_format_deposit_failure_user_banned_says_blacklisted() -> None:
    from deathroll_deposit_withdraw.cogs.deposit import _format_deposit_failure
    from deathroll_deposit_withdraw.tickets.orchestration import DepositOutcome

    msg = _format_deposit_failure(DepositOutcome.UserBanned())
    assert "blacklist" in msg.lower()


def test_format_withdraw_failure_user_banned_says_blacklisted() -> None:
    from deathroll_deposit_withdraw.cogs.withdraw import _format_withdraw_failure
    from deathroll_deposit_withdraw.tickets.orchestration import WithdrawOutcome

    msg = _format_withdraw_failure(WithdrawOutcome.UserBanned())
    assert "blacklist" in msg.lower()
