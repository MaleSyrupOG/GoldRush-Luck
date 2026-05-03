"""Admin cog — every ``/admin *`` command (Epic 10 + Epic 9 dispute slice).

Story 10.1 lands the foundational ``/admin-setup``; Stories 10.4,
10.5 and 10.7 add the operational toolkit (force-cashier-offline,
cashier-stats, force-cancel-ticket, force-close-thread). Epic 9's
Story 9.1 adds the dispute commands (open / list / resolve / reject)
because they are admin-only by spec §5.1 and live alongside the
other admin tooling. The remaining Epic 10 stories (10.2 set-limits,
10.3 set-guides, 10.6 treasury, 10.8 view-audit) are deferred —
admins can edit ``dw.global_config`` / ``dw.dynamic_embeds`` via SQL
until they land.

All admin commands are hidden from non-admins by default via
``@app_commands.default_permissions(administrator=True)``. Aleix
configures visibility per-role manually in *Server Settings →
Integrations* — that's the canonical mechanism for v1 (spec §6.5
calls out that we deliberately do NOT request the ``Manage Roles``
intent the bot would need to manage @admin role membership itself).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    ban_user,
    cancel_deposit,
    cancel_withdraw,
    open_dispute,
    reject_dispute,
    resolve_dispute,
    set_cashier_status,
    unban_user,
)
from goldrush_core.embeds.dw_tickets import (
    cashier_stats_embed,
    dispute_list_embed,
    dispute_resolved_embed,
)

from goldrush_deposit_withdraw.audit_log import (
    audit_dispute_opened,
    audit_dispute_rejected,
    audit_dispute_resolved,
    audit_force_cancel_ticket,
    audit_force_cashier_offline,
    audit_force_close_thread,
    audit_user_banned,
    audit_user_unbanned,
)
from goldrush_deposit_withdraw.disputes import (
    post_dispute_open_embed,
    update_dispute_status_embed,
)
from goldrush_deposit_withdraw.setup.channel_factory import (
    SetupReport,
    setup_or_reuse_channels,
)
from goldrush_deposit_withdraw.setup.global_config_writer import (
    persist_channel_ids,
    persist_role_ids,
)
from goldrush_deposit_withdraw.welcome import reconcile_welcome_embeds
from goldrush_deposit_withdraw.workers.audit_chain_verifier import (
    tick as verify_audit_chain_tick,
)

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
        await audit_force_cashier_offline(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            cashier_mention=cashier.mention,
            reason=reason,
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
        await audit_force_cancel_ticket(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            ticket_uid=ticket_uid,
            reason=reason,
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
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is not None:
            await audit_force_close_thread(
                pool=bot.pool,
                bot=bot,
                admin_mention=interaction.user.mention,
                thread_mention=thread.mention,
                reason=reason,
            )
        _log.info(
            "admin_force_close_thread",
            actor_id=interaction.user.id,
            thread_id=thread.id,
            reason=reason,
        )

    # -----------------------------------------------------------------------
    # Story 9.1: /admin-dispute-{open,list,resolve,reject}
    # The dispute lifecycle is admin-only (spec §5.1). The four commands
    # mirror the SECURITY DEFINER fns in migrations 0010 + 0013.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-dispute-open",
        description="Open a dispute on a ticket (writes audit row + #disputes embed).",
    )
    @app_commands.describe(
        ticket_type="deposit or withdraw",
        ticket_uid="The ticket UID (e.g., deposit-12).",
        reason="Why this dispute is being opened.",
    )
    async def dispute_open_cmd(
        self,
        interaction: discord.Interaction,
        ticket_type: Literal["deposit", "withdraw"],
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            dispute_id = await open_dispute(
                bot.pool,
                ticket_type=ticket_type,
                ticket_uid=ticket_uid,
                opener_id=interaction.user.id,
                opener_role="admin",
                reason=reason,
            )
        except exc.TicketNotFound:
            await interaction.followup.send(
                f"❌ Ticket `{ticket_uid}` not found.", ephemeral=True
            )
            return
        except exc.BalanceError as e:
            await interaction.followup.send(
                f"❌ Could not open dispute: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Dispute `#{dispute_id}` opened on `{ticket_uid}`.",
            ephemeral=True,
        )
        # Story 9.2: post the long-lived embed in #disputes (persists
        # message_id) and the transient note in #audit-log.
        await post_dispute_open_embed(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            opener_mention=interaction.user.mention,
            opener_role="admin",
            reason=reason,
            opened_at=discord.utils.utcnow(),
        )
        await audit_dispute_opened(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            opener_mention=interaction.user.mention,
            opener_role="admin",
            reason=reason,
        )
        _log.info(
            "admin_dispute_opened",
            actor_id=interaction.user.id,
            dispute_id=dispute_id,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
        )

    @app_commands.command(
        name="admin-dispute-list",
        description="List recent disputes (optionally filtered by status).",
    )
    @app_commands.describe(
        status=(
            "Filter to one status. Omit to see every status. "
            "Cap is 25 most recent rows."
        ),
    )
    async def dispute_list_cmd(
        self,
        interaction: discord.Interaction,
        status: Literal["open", "investigating", "resolved", "rejected"] | None = None,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        # Cap at 25 to stay below Discord's per-embed field cap and keep
        # the list scannable; admins paginate by status filter.
        if status is None:
            rows = await bot.pool.fetch(
                """
                SELECT id, ticket_type, ticket_uid, status, opener_id, opened_at
                  FROM dw.disputes
                 ORDER BY opened_at DESC
                 LIMIT 25
                """
            )
        else:
            rows = await bot.pool.fetch(
                """
                SELECT id, ticket_type, ticket_uid, status, opener_id, opened_at
                  FROM dw.disputes
                 WHERE status = $1
                 ORDER BY opened_at DESC
                 LIMIT 25
                """,
                status,
            )
        embed = dispute_list_embed(
            disputes=[dict(r) for r in rows],
            status_filter=status,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="admin-dispute-resolve",
        description="Resolve a dispute. Optional refund routes via the treasury.",
    )
    @app_commands.describe(
        dispute_id="The dispute id (from /admin-dispute-list).",
        action="How the dispute resolved.",
        amount="Required only for partial-refund (gold amount).",
    )
    async def dispute_resolve_cmd(
        self,
        interaction: discord.Interaction,
        dispute_id: int,
        action: Literal["no-action", "refund-full", "force-confirm", "partial-refund"],
        amount: int | None = None,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await resolve_dispute(
                bot.pool,
                dispute_id=dispute_id,
                action=action,
                amount=amount,
                resolved_by=interaction.user.id,
            )
        except exc.DisputeNotFound:
            await interaction.followup.send(
                f"❌ Dispute `#{dispute_id}` not found.",
                ephemeral=True,
            )
            return
        except exc.DisputeAlreadyTerminal:
            await interaction.followup.send(
                f"❌ Dispute `#{dispute_id}` is already in a terminal state.",
                ephemeral=True,
            )
            return
        except exc.PartialRefundRequiresPositiveAmount:
            await interaction.followup.send(
                "❌ `partial-refund` requires a positive `amount`.",
                ephemeral=True,
            )
            return
        except exc.RefundFullOnlyForWithdrawDisputes:
            await interaction.followup.send(
                "❌ `refund-full` is only valid for withdraw disputes.",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.followup.send(
                f"❌ Could not resolve: {e.message}",
                ephemeral=True,
            )
            return
        # Look up the ticket_uid for the audit poster — pure read, never
        # blocks the resolve flow.
        ticket_uid = await bot.pool.fetchval(
            "SELECT ticket_uid FROM dw.disputes WHERE id = $1",
            dispute_id,
        )
        ticket_uid_str = str(ticket_uid) if ticket_uid is not None else "?"
        await interaction.followup.send(
            f"✅ Dispute `#{dispute_id}` resolved as `{action}`.",
            ephemeral=True,
        )
        # Story 9.2: edit the existing #disputes embed in place rather
        # than posting a new one. Best-effort — failures are logged.
        await update_dispute_status_embed(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            new_embed=dispute_resolved_embed(
                dispute_id=dispute_id,
                ticket_uid=ticket_uid_str,
                resolution=(
                    f"action={action}"
                    + (f", amount={amount:,}g" if amount else "")
                ),
                resolved_by_mention=interaction.user.mention,
                resolved_at=discord.utils.utcnow(),
                status="resolved",
            ),
        )
        await audit_dispute_resolved(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            ticket_uid=ticket_uid_str,
            admin_mention=interaction.user.mention,
            action=action,
            amount=amount,
        )
        _log.info(
            "admin_dispute_resolved",
            actor_id=interaction.user.id,
            dispute_id=dispute_id,
            action=action,
            amount=amount,
        )

    @app_commands.command(
        name="admin-dispute-reject",
        description="Reject a dispute (close-without-resolution; no money moves).",
    )
    @app_commands.describe(
        dispute_id="The dispute id.",
        reason="Why the dispute is rejected (visible in audit log).",
    )
    async def dispute_reject_cmd(
        self,
        interaction: discord.Interaction,
        dispute_id: int,
        reason: str,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await reject_dispute(
                bot.pool,
                dispute_id=dispute_id,
                reason=reason,
                admin_id=interaction.user.id,
            )
        except exc.DisputeNotFound:
            await interaction.followup.send(
                f"❌ Dispute `#{dispute_id}` not found.",
                ephemeral=True,
            )
            return
        except exc.DisputeAlreadyTerminal:
            await interaction.followup.send(
                f"❌ Dispute `#{dispute_id}` is already in a terminal state.",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.followup.send(
                f"❌ Could not reject: {e.message}",
                ephemeral=True,
            )
            return
        ticket_uid = await bot.pool.fetchval(
            "SELECT ticket_uid FROM dw.disputes WHERE id = $1",
            dispute_id,
        )
        ticket_uid_str = str(ticket_uid) if ticket_uid is not None else "?"
        await interaction.followup.send(
            f"✅ Dispute `#{dispute_id}` rejected.",
            ephemeral=True,
        )
        # Story 9.2: edit the existing #disputes embed in place.
        await update_dispute_status_embed(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            new_embed=dispute_resolved_embed(
                dispute_id=dispute_id,
                ticket_uid=ticket_uid_str,
                resolution=reason,
                resolved_by_mention=interaction.user.mention,
                resolved_at=discord.utils.utcnow(),
                status="rejected",
            ),
        )
        await audit_dispute_rejected(
            pool=bot.pool,
            bot=bot,
            dispute_id=dispute_id,
            ticket_uid=ticket_uid_str,
            admin_mention=interaction.user.mention,
            reason=reason,
        )
        _log.info(
            "admin_dispute_rejected",
            actor_id=interaction.user.id,
            dispute_id=dispute_id,
            reason=reason,
        )

    # -----------------------------------------------------------------------
    # Story 9.3: /admin-ban-user and /admin-unban-user
    # The SECURITY DEFINER fns idempotently insert the user row so a
    # pre-emptive ban (against a user the bot has never seen) Just Works.
    # After ban, the deposit + withdraw create-ticket fns reject with
    # ``user_banned`` which the cogs surface as a "blacklisted" ephemeral.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-ban-user",
        description="Blacklist a user from /deposit and /withdraw.",
    )
    @app_commands.describe(
        user="The user to blacklist.",
        reason="Why (visible in audit log).",
    )
    async def ban_user_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.User,
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
            await ban_user(
                bot.pool,
                user_id=user.id,
                reason=reason,
                admin_id=interaction.user.id,
            )
        except exc.CannotBanTreasury:
            await interaction.response.send_message(
                "❌ The treasury account cannot be banned.",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not ban: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🚫 {user.mention} is now blacklisted. Reason: {reason}",
            ephemeral=True,
        )
        await audit_user_banned(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            target_mention=user.mention,
            reason=reason,
        )
        _log.info(
            "admin_user_banned",
            actor_id=interaction.user.id,
            target_id=user.id,
            reason=reason,
        )

    @app_commands.command(
        name="admin-verify-audit",
        description="Walk core.audit_log recomputing the HMAC chain (on-demand).",
    )
    async def verify_audit_cmd(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Story 8.6: on-demand verifier. Runs the same SDF the
        background worker does and reports the outcome inline."""
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await verify_audit_chain_tick(pool=bot.pool)
        except Exception as e:
            _log.exception("admin_verify_audit_failed", error=str(e))
            await interaction.followup.send(
                f"❌ Verifier crashed: `{type(e).__name__}` — see logs.",
                ephemeral=True,
            )
            return

        if result.broken_at_id is not None:
            await interaction.followup.send(
                f"🚨 **Chain break detected at id `{result.broken_at_id}`** — "
                f"checked {result.checked_count} rows; last verified id is "
                f"`{result.last_verified_id}`. Investigate immediately.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Chain verified across {result.checked_count} new row(s). "
            f"Last verified id: `{result.last_verified_id}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="admin-unban-user",
        description="Lift the blacklist from a user.",
    )
    @app_commands.describe(user="The user to unban.")
    async def unban_user_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        try:
            await unban_user(
                bot.pool,
                user_id=user.id,
                admin_id=interaction.user.id,
            )
        except exc.UserNotRegistered:
            await interaction.response.send_message(
                f"❌ {user.mention} has no balance row — never banned to begin with.",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not unban: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ {user.mention} is no longer blacklisted.",
            ephemeral=True,
        )
        await audit_user_unbanned(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            target_mention=user.mention,
        )
        _log.info(
            "admin_user_unbanned",
            actor_id=interaction.user.id,
            target_id=user.id,
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
