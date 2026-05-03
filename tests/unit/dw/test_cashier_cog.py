"""Cog-registration tests for the cashier cog (Epic 7).

The cog ships five slash commands. End-to-end interaction is
exercised in Epic 14 integration tests; here we guard the
structural contract.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands
from deathroll_deposit_withdraw.cogs.cashier import CashierCog


def _build_bot() -> commands.Bot:
    return commands.Bot(
        command_prefix="!unused",
        intents=discord.Intents.default(),
    )


def test_cashier_cog_registers_all_five_commands() -> None:
    """Spec §5.1: addchar / removechar / listchars / set-status / mystats."""
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(CashierCog(bot))
        cog = bot.get_cog("CashierCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    expected = {
        "cashier-addchar",
        "cashier-removechar",
        "cashier-listchars",
        "cashier-set-status",
        "cashier-mystats",
    }
    assert names == expected


def test_addchar_takes_char_realm_region_faction_parameters() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(CashierCog(bot))
        cog = bot.get_cog("CashierCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "cashier-addchar")
        return {p.name for p in cmd.parameters}

    params = asyncio.run(_exercise())
    assert params == {"char", "realm", "region", "faction"}


def test_set_status_takes_status_with_three_choices() -> None:
    bot = _build_bot()

    async def _exercise() -> tuple[str, ...]:
        await bot.add_cog(CashierCog(bot))
        cog = bot.get_cog("CashierCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "cashier-set-status")
        status_param = next(p for p in cmd.parameters if p.name == "status")
        # discord.py exposes the bound choices via the parameter's
        # ``choices`` attribute when @app_commands.choices is used.
        return tuple(c.value for c in status_param.choices)

    values = asyncio.run(_exercise())
    assert set(values) == {"online", "offline", "break"}


def test_listchars_takes_no_user_parameters() -> None:
    bot = _build_bot()

    async def _exercise() -> int:
        await bot.add_cog(CashierCog(bot))
        cog = bot.get_cog("CashierCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "cashier-listchars")
        return len(cmd.parameters)

    assert asyncio.run(_exercise()) == 0
