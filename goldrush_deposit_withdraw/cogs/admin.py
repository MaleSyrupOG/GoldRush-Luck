"""Admin cog — every ``/admin *`` command (Epic 10).

Story 10.1 lands the foundational ``/admin-setup``; Stories 10.4,
10.5 and 10.7 add the operational toolkit (force-cashier-offline,
cashier-stats, force-cancel-ticket, force-close-thread). The
remaining Epic 10 stories (10.2 set-limits, 10.3 set-guides,
10.6 treasury, 10.8 view-audit) are deferred — admins can edit
``dw.global_config`` / ``dw.dynamic_embeds`` via SQL until they
land.

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
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    cancel_deposit,
    cancel_withdraw,
    set_cashier_status,
)
from goldrush_core.embeds.dw_tickets import cashier_stats_embed

from goldrush_deposit_withdraw.setup.channel_factory import (
    SetupReport,
    setup_or_reuse_channels,
)
from goldrush_deposit_withdraw.setup.global_config_writer import (
    persist_channel_ids,
    persist_role_ids,
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

    # -----------------------------------------------------------------------
    # Story 10.1: /admin-setup — channel auto-creation + welcome reconcile
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-setup",
        description="Create / reuse the canonical D/W channels and persist their ids.",
    )
    @app_commands.describe(
        dry_run="If True, preview the plan without creating anything.",
        cashier_role="The @cashier role (optional — pass for full overwrites).",
        admin_role="The @admin role (optional — pass for full overwrites).",
    )
    async def setup_cmd(
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

        # Persist role ids alongside channel ids so the rest of the bot
        # can render real ``<@&role_id>`` mentions (instead of literal
        # ``@cashier`` strings, which Discord treats as plain text and
        # never pings). Skipped on dry-run.
        if not dry_run:
            role_id_map: dict[str, int] = {}
            if cashier_role is not None:
                role_id_map["cashier"] = cashier_role.id
            if admin_role is not None:
                role_id_map["admin"] = admin_role.id
            if role_id_map:
                await persist_role_ids(
                    bot.pool,
                    role_id_map=role_id_map,
                    actor_id=interaction.user.id,
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

    # -----------------------------------------------------------------------
    # Story 10.4: force-cashier-offline (+ informational promote/demote)
    # spec §6.5: bot does NOT hold Manage Roles, so promote/demote are
    # informational reminders pointing at *Server Settings → Members*.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-force-cashier-offline",
        description="Force a cashier to offline status (writes audit row).",
    )
    @app_commands.describe(
        cashier="The cashier to take offline.",
        reason="Why (visible in audit log).",
    )
    async def force_cashier_offline(
        self,
        interaction: discord.Interaction,
        cashier: discord.Member,
        reason: str,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        try:
            await set_cashier_status(
                bot.pool,
                discord_id=cashier.id,
                status="offline",
            )
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not set offline: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ {cashier.mention} is now offline. Reason logged: {reason}",
            ephemeral=True,
        )
        _log.info(
            "admin_force_cashier_offline",
            actor_id=interaction.user.id,
            cashier_id=cashier.id,
            reason=reason,
        )

    @app_commands.command(
        name="admin-promote-cashier",
        description="Reminder: add the @cashier role via Server Settings → Roles.",
    )
    @app_commands.describe(user="The user you want to promote.")
    async def promote_cashier(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.send_message(
            f"To promote {user.mention} to cashier, add them to the "
            f"`@cashier` role via *Server Settings → Members*.\n"
            f"The bot deliberately does not manage roles "
            f"(spec §6.5 — Manage Roles intent is forbidden).",
            ephemeral=True,
        )

    @app_commands.command(
        name="admin-demote-cashier",
        description="Reminder: remove the @cashier role via Server Settings → Roles.",
    )
    @app_commands.describe(user="The user you want to demote.")
    async def demote_cashier(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.send_message(
            f"To demote {user.mention}, remove them from the `@cashier` "
            f"role via *Server Settings → Members*. After removal, run "
            f"`/admin-force-cashier-offline` so the audit log records "
            f"the transition.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # Story 10.5: cashier-stats @cashier (admin view)
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-cashier-stats",
        description="Show stats for any cashier (admin ephemeral).",
    )
    @app_commands.describe(cashier="The cashier whose stats to view.")
    async def cashier_stats_cmd(
        self,
        interaction: discord.Interaction,
        cashier: discord.Member,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        row = await bot.pool.fetchrow(
            """
            SELECT deposits_completed, deposits_cancelled,
                   withdraws_completed, withdraws_cancelled,
                   total_volume_g, total_online_seconds,
                   avg_claim_to_confirm_s, last_active_at
              FROM dw.cashier_stats
              WHERE discord_id = $1
            """,
            cashier.id,
        )
        embed = _admin_cashier_stats_embed(
            cashier_mention=cashier.mention, row=row
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # Story 10.7: force-cancel-ticket / force-close-thread
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-force-cancel-ticket",
        description="Force-cancel a stuck ticket regardless of state.",
    )
    @app_commands.describe(
        ticket_uid="The ticket UID (e.g., deposit-12 or withdraw-5).",
        reason="Why (visible in audit log).",
    )
    async def force_cancel_ticket(
        self,
        interaction: discord.Interaction,
        ticket_uid: str,
        reason: str,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        # The cancel fn itself rejects already-terminal tickets — so a
        # double-force surfaces a clear error rather than silently
        # double-cancelling.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if ticket_uid.startswith("deposit-"):
                await cancel_deposit(
                    bot.pool,
                    ticket_uid=ticket_uid,
                    actor_id=interaction.user.id,
                    reason=f"admin force: {reason}",
                )
            elif ticket_uid.startswith("withdraw-"):
                await cancel_withdraw(
                    bot.pool,
                    ticket_uid=ticket_uid,
                    actor_id=interaction.user.id,
                    reason=f"admin force: {reason}",
                )
            else:
                await interaction.followup.send(
                    f"❌ Unknown ticket prefix in `{ticket_uid}`. "
                    f"Expected `deposit-N` or `withdraw-N`.",
                    ephemeral=True,
                )
                return
        except exc.TicketNotFound:
            await interaction.followup.send(
                f"❌ Ticket `{ticket_uid}` not found.",
                ephemeral=True,
            )
            return
        except exc.TicketAlreadyTerminal:
            await interaction.followup.send(
                f"❌ Ticket `{ticket_uid}` is already in a terminal state.",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.followup.send(
                f"❌ Could not force-cancel: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Force-cancelled `{ticket_uid}`. Reason: {reason}",
            ephemeral=True,
        )
        _log.info(
            "admin_force_cancel_ticket",
            actor_id=interaction.user.id,
            ticket_uid=ticket_uid,
            reason=reason,
        )

    @app_commands.command(
        name="admin-force-close-thread",
        description="Archive a stuck thread without changing balance (audit only).",
    )
    @app_commands.describe(
        thread="The thread to archive.",
        reason="Why (visible in audit log).",
    )
    async def force_close_thread(
        self,
        interaction: discord.Interaction,
        thread: discord.Thread,
        reason: str,
    ) -> None:
        try:
            await thread.edit(archived=True, reason=f"admin force: {reason}")
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Could not archive: {e}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Archived {thread.mention}. Reason: {reason}",
            ephemeral=True,
        )
        _log.info(
            "admin_force_close_thread",
            actor_id=interaction.user.id,
            thread_id=thread.id,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _admin_cashier_stats_embed(*, cashier_mention: str, row: object) -> discord.Embed:
    """Reuse the cashier-mystats embed builder for the admin view."""
    if row is None:
        return cashier_stats_embed(
            cashier_mention=cashier_mention,
            deposits_completed=0,
            deposits_cancelled=0,
            withdraws_completed=0,
            withdraws_cancelled=0,
            total_volume_g=0,
            total_online_seconds=0,
            avg_claim_to_confirm_s=None,
            last_active_at=None,
        )
    return cashier_stats_embed(
        cashier_mention=cashier_mention,
        deposits_completed=int(row["deposits_completed"]),  # type: ignore[index]
        deposits_cancelled=int(row["deposits_cancelled"]),  # type: ignore[index]
        withdraws_completed=int(row["withdraws_completed"]),  # type: ignore[index]
        withdraws_cancelled=int(row["withdraws_cancelled"]),  # type: ignore[index]
        total_volume_g=int(row["total_volume_g"]),  # type: ignore[index]
        total_online_seconds=int(row["total_online_seconds"]),  # type: ignore[index]
        avg_claim_to_confirm_s=(
            int(row["avg_claim_to_confirm_s"])  # type: ignore[index]
            if row["avg_claim_to_confirm_s"] is not None  # type: ignore[index]
            else None
        ),
        last_active_at=row["last_active_at"],  # type: ignore[index]
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
