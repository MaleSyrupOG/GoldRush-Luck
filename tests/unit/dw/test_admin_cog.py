"""Cog-registration tests for the admin cog (Story 10.1).

End-to-end ``/admin setup`` is exercised in Epic 14 (it interacts
with a real guild). Here we guard the structural contract: the
cog ships exactly the slash commands we expect and they have the
right parameter shape.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands
from goldrush_deposit_withdraw.cogs.admin import AdminCog


def _build_bot() -> commands.Bot:
    return commands.Bot(
        command_prefix="!unused",
        intents=discord.Intents.default(),
    )


def test_admin_cog_registers_setup_command() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert "admin-setup" in names


def test_admin_setup_takes_optional_dry_run_and_role_parameters() -> None:
    """Story 10.1 AC: ``--dry-run`` mode shows preview without creating."""
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-setup")
        return {p.name for p in cmd.parameters}

    params = asyncio.run(_exercise())
    assert {"dry_run", "cashier_role", "admin_role"}.issubset(params)


def test_admin_setup_dry_run_parameter_is_optional() -> None:
    bot = _build_bot()

    async def _exercise() -> bool:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-setup")
        param = next(p for p in cmd.parameters if p.name == "dry_run")
        return param.required

    assert asyncio.run(_exercise()) is False


def test_admin_cog_registers_the_full_operational_toolkit() -> None:
    """Stories 10.4 + 10.5 + 10.7: every admin operational command
    is registered in addition to /admin-setup."""
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    expected = {
        "admin-setup",                      # Story 10.1
        "admin-force-cashier-offline",      # Story 10.4
        "admin-promote-cashier",            # Story 10.4 (informational)
        "admin-demote-cashier",             # Story 10.4 (informational)
        "admin-cashier-stats",              # Story 10.5
        "admin-force-cancel-ticket",        # Story 10.7
        "admin-force-close-thread",         # Story 10.7
    }
    assert expected.issubset(names)


def test_force_cashier_offline_takes_cashier_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-force-cashier-offline"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"cashier", "reason"}


def test_force_cancel_ticket_takes_uid_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-force-cancel-ticket"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"ticket_uid", "reason"}
