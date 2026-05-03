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


# ---------------------------------------------------------------------------
# Story 9.1 — /admin dispute open / list / resolve / reject
# ---------------------------------------------------------------------------


def test_admin_cog_registers_the_dispute_commands() -> None:
    """Story 9.1 AC: four dispute commands are exposed via slash."""
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    expected = {
        "admin-dispute-open",
        "admin-dispute-list",
        "admin-dispute-resolve",
        "admin-dispute-reject",
    }
    assert expected.issubset(names)


def test_admin_dispute_open_takes_type_uid_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-dispute-open")
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"ticket_type", "ticket_uid", "reason"}


def test_admin_dispute_list_status_filter_is_optional() -> None:
    bot = _build_bot()

    async def _exercise() -> tuple[set[str], bool]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-dispute-list")
        params = {p.name for p in cmd.parameters}
        status_param = next(p for p in cmd.parameters if p.name == "status")
        return params, status_param.required

    params, required = asyncio.run(_exercise())
    assert "status" in params
    assert required is False


def test_admin_dispute_resolve_takes_id_action_optional_amount() -> None:
    bot = _build_bot()

    async def _exercise() -> tuple[set[str], bool]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-dispute-resolve"
        )
        params = {p.name for p in cmd.parameters}
        amount_param = next(p for p in cmd.parameters if p.name == "amount")
        return params, amount_param.required

    params, required = asyncio.run(_exercise())
    assert {"dispute_id", "action", "amount"}.issubset(params)
    assert required is False


def test_admin_dispute_reject_takes_id_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-dispute-reject")
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"dispute_id", "reason"}


# ---------------------------------------------------------------------------
# Story 9.3 — /admin-ban-user / /admin-unban-user
# ---------------------------------------------------------------------------


def test_admin_cog_registers_ban_and_unban_commands() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert {"admin-ban-user", "admin-unban-user"}.issubset(names)


def test_admin_ban_user_takes_user_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-ban-user")
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"user", "reason"}


def test_admin_unban_user_takes_user_only() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-unban-user")
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"user"}


# ---------------------------------------------------------------------------
# Story 8.6 — /admin-verify-audit (on-demand audit chain verification)
# ---------------------------------------------------------------------------


def test_admin_cog_registers_verify_audit_command() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert "admin-verify-audit" in names


# ---------------------------------------------------------------------------
# Story 10.2 — set-deposit-limits / set-withdraw-limits / set-fee-withdraw
# ---------------------------------------------------------------------------


def test_admin_cog_registers_set_limits_and_fee_commands() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    expected = {
        "admin-set-deposit-limits",
        "admin-set-withdraw-limits",
        "admin-set-fee-withdraw",
    }
    assert expected.issubset(names)


def test_admin_set_deposit_limits_takes_min_and_max() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-set-deposit-limits"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"min_g", "max_g"}


def test_admin_set_withdraw_limits_takes_min_and_max() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-set-withdraw-limits"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"min_g", "max_g"}


def test_admin_set_fee_withdraw_takes_bps_only() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-set-fee-withdraw"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"bps"}


# ---------------------------------------------------------------------------
# Story 10.3 — set-deposit-guide / set-withdraw-guide modals
# ---------------------------------------------------------------------------


def test_admin_cog_registers_set_guide_commands() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    assert {"admin-set-deposit-guide", "admin-set-withdraw-guide"}.issubset(names)


def test_admin_set_guide_commands_take_no_parameters() -> None:
    """The modal carries the input — slash command surfaces zero args."""
    bot = _build_bot()

    async def _exercise() -> tuple[int, int]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        deposit = next(
            c for c in cog.get_app_commands() if c.name == "admin-set-deposit-guide"
        )
        withdraw = next(
            c for c in cog.get_app_commands() if c.name == "admin-set-withdraw-guide"
        )
        return len(deposit.parameters), len(withdraw.parameters)

    deposit_args, withdraw_args = asyncio.run(_exercise())
    assert deposit_args == 0
    assert withdraw_args == 0


# ---------------------------------------------------------------------------
# Story 10.6 — treasury-balance / treasury-sweep / treasury-withdraw-to-user
# ---------------------------------------------------------------------------


def test_admin_cog_registers_treasury_commands() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        return {cmd.name for cmd in cog.get_app_commands()}

    names = asyncio.run(_exercise())
    expected = {
        "admin-treasury-balance",
        "admin-treasury-sweep",
        "admin-treasury-withdraw-to-user",
    }
    assert expected.issubset(names)


def test_admin_treasury_balance_takes_no_parameters() -> None:
    bot = _build_bot()

    async def _exercise() -> int:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-treasury-balance")
        return len(cmd.parameters)

    assert asyncio.run(_exercise()) == 0


def test_admin_treasury_sweep_takes_amount_and_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(c for c in cog.get_app_commands() if c.name == "admin-treasury-sweep")
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"amount", "reason"}


def test_admin_treasury_withdraw_takes_amount_user_reason() -> None:
    bot = _build_bot()

    async def _exercise() -> set[str]:
        await bot.add_cog(AdminCog(bot))
        cog = bot.get_cog("AdminCog")
        assert cog is not None
        cmd = next(
            c for c in cog.get_app_commands() if c.name == "admin-treasury-withdraw-to-user"
        )
        return {p.name for p in cmd.parameters}

    assert asyncio.run(_exercise()) == {"amount", "user", "reason"}
