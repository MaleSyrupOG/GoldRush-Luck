"""Admin cog — every ``/admin *`` command (Epic 10).

Story 10.1 lands the foundational ``/admin setup`` command. Other
admin commands (treasury, dispute, force-cancel, set-limits) land
in subsequent stories; their slash command surfaces are wired into
the same cog.

All admin commands are hidden from non-admins by default via
``@app_commands.default_permissions(administrator=True)``. Aleix
configures visibility per-role manually in *Server Settings →
Integrations* — that's the canonical mechanism for v1 (spec §6.5
calls out that we deliberately do NOT request the ``Manage Roles``
intent the bot would need to manage @admin role membership itself).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from goldrush_deposit_withdraw.setup.channel_factory import (
    SetupReport,
    setup_or_reuse_channels,
)
from goldrush_deposit_withdraw.setup.global_config_writer import (
    persist_channel_ids,
)
from goldrush_deposit_withdraw.welcome import reconcile_welcome_embeds

if TYPE_CHECKING:
    from goldrush_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


@app_commands.default_permissions(administrator=True)
class AdminCog(commands.Cog):
    """Hosts every ``/admin *`` command. Hidden from non-admins by default."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="admin-setup",
        description="Create / reuse the canonical D/W channels and persist their ids.",
    )
    @app_commands.describe(
        dry_run="If True, preview the plan without creating anything.",
        cashier_role="The @cashier role (optional — pass for full overwrites).",
        admin_role="The @admin role (optional — pass for full overwrites).",
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        dry_run: bool = False,
        cashier_role: discord.Role | None = None,
        admin_role: discord.Role | None = None,
    ) -> None:
        """Provision (or reuse) the canonical ``Banking`` and ``Cashier``
        categories plus the eight channels per spec §5.3.

        After channels exist, persists every id into
        ``dw.global_config`` (so the rest of the bot self-configures)
        and triggers the welcome-embed reconciler.
        """
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        # Defer because real setup creates 10 entities and each is an
        # API round-trip — well over Discord's 3-second initial reply
        # window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def _persist(channel_id_map: dict[str, int]) -> None:
            assert bot.pool is not None
            await persist_channel_ids(
                bot.pool,
                channel_id_map=channel_id_map,
                actor_id=interaction.user.id,
            )

        report = await setup_or_reuse_channels(
            guild,
            cashier_role_id=cashier_role.id if cashier_role else None,
            admin_role_id=admin_role.id if admin_role else None,
            dry_run=dry_run,
            persist=_persist if not dry_run else None,
        )

        # On real run, reconcile the welcome embeds immediately so the
        # operator sees the bot's full state in one command. Best-effort:
        # the next on_ready will also retry.
        welcome_summary: str | None = None
        if not dry_run:
            try:
                outcomes = await reconcile_welcome_embeds(pool=bot.pool, bot=bot)
                welcome_summary = ", ".join(
                    f"{o.embed_key}={o.action}" for o in outcomes
                )
            except Exception as e:
                _log.exception("welcome_embeds_failed_post_setup", error=str(e))
                welcome_summary = "(reconciler errored — see logs)"

        embed = _build_setup_report_embed(
            report=report,
            welcome_summary=welcome_summary,
            dry_run=dry_run,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        _log.info(
            "admin_setup_complete",
            actor_id=interaction.user.id,
            dry_run=dry_run,
            created_count=report.created_count,
            reused_count=report.reused_count,
        )


def _build_setup_report_embed(
    *,
    report: SetupReport,
    welcome_summary: str | None,
    dry_run: bool,
) -> discord.Embed:
    """Render the SetupReport as an admin-facing summary embed.

    Lists each category and channel with a ``created`` / ``reused``
    flag so the operator sees exactly what changed. For dry-run the
    counts read as "would create" / "already present".
    """
    title = (
        "🛠️ /admin setup — DRY RUN preview"
        if dry_run
        else "✅ /admin setup complete"
    )
    color = 0xC8511C if dry_run else 0x5DBE5A
    if dry_run:
        description = (
            f"Would create **{report.created_count}** entities; "
            f"**{report.reused_count}** already present."
        )
    else:
        description = (
            f"Created **{report.created_count}** entities; "
            f"reused **{report.reused_count}**."
        )
    embed = discord.Embed(title=title, description=description, color=color)

    cat_lines = [
        f"• `{c.key}` ({c.name}) — {'created' if c.created else 'reused'}"
        for c in report.categories
    ]
    embed.add_field(name="Categories", value="\n".join(cat_lines), inline=False)

    ch_lines = [
        f"• `{c.key}` (#{c.name}) — {'created' if c.created else 'reused'}"
        for c in report.channels
    ]
    embed.add_field(name="Channels", value="\n".join(ch_lines), inline=False)

    if welcome_summary:
        embed.add_field(
            name="Welcome embeds",
            value=welcome_summary,
            inline=False,
        )
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
