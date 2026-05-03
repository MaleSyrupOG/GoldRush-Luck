"""Admin cog — every ``/admin *`` command (Epics 9 + 10 + Story 8.6 verifier).

The cog is the home for every admin-only slash command:

- Story 10.1 — ``/admin-setup`` (channel + role provisioning).
- Stories 10.4 / 10.5 / 10.7 — operational toolkit
  (force-cashier-offline, promote/demote reminders, cashier-stats,
  force-cancel-ticket, force-close-thread).
- Story 9.1 — dispute lifecycle (``/admin-dispute-{open, list,
  resolve, reject}``).
- Story 9.3 — blacklist (``/admin-{ban, unban}-user``).
- Story 8.6 — on-demand chain verifier (``/admin-verify-audit``).
- Story 10.2 — config writes
  (``/admin-set-{deposit, withdraw}-limits``,
  ``/admin-set-fee-withdraw``).
- Story 10.3 — dynamic-embed copy editor
  (``/admin-set-{deposit, withdraw}-guide`` modals).
- Story 10.6 — treasury operations with 2FA gates
  (``/admin-treasury-{balance, sweep, withdraw-to-user}``).
- Story 10.8 — audit log read (``/admin-view-audit``).

All commands are hidden from non-admins by default via
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
    list_audit_events,
    open_dispute,
    reject_dispute,
    resolve_dispute,
    set_cashier_status,
    treasury_sweep,
    treasury_withdraw_to_user,
    unban_user,
)
from goldrush_core.embeds.dw_tickets import (
    audit_log_list_embed,
    cashier_stats_embed,
    dispute_list_embed,
    dispute_resolved_embed,
)

from goldrush_deposit_withdraw.audit_log import (
    audit_config_changed,
    audit_dispute_opened,
    audit_dispute_rejected,
    audit_dispute_resolved,
    audit_force_cancel_ticket,
    audit_force_cashier_offline,
    audit_force_close_thread,
    audit_treasury_sweep,
    audit_treasury_withdraw_to_user,
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
    persist_config_int,
    persist_role_ids,
)
from goldrush_deposit_withdraw.views.modals import (
    EditDynamicEmbedModal,
    TreasurySweepConfirmModal,
    TreasuryWithdrawConfirmModal,
)
from goldrush_deposit_withdraw.welcome import (
    reconcile_welcome_embeds,
    update_dynamic_embed_content,
)
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

    # -----------------------------------------------------------------------
    # Story 10.8: /admin-view-audit — paginated tail of core.audit_log.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-view-audit",
        description="Show recent audit-log events (optionally filter by user).",
    )
    @app_commands.describe(
        user="Filter to events targeting this user (optional).",
        limit="Number of rows to show (1-100, default 25).",
    )
    async def view_audit_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        limit: int | None = None,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        # The SDF clamps to [1, 100]; we additionally cap the embed
        # render at 25 rows to stay under Discord's 6000-char total
        # embed limit (each row is ~120 chars in the markdown list).
        effective_limit = max(1, min(limit or 25, 25))
        rows = await list_audit_events(
            bot.pool,
            target_id=user.id if user is not None else None,
            limit=effective_limit,
        )
        embed = audit_log_list_embed(
            rows=rows,
            target_filter=user.id if user is not None else None,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        _log.info(
            "admin_view_audit",
            actor_id=interaction.user.id,
            target_id=user.id if user is not None else None,
            limit=effective_limit,
            row_count=len(rows),
        )

    # -----------------------------------------------------------------------
    # Story 10.3: /admin-set-deposit-guide, /admin-set-withdraw-guide
    # Open EditDynamicEmbedModal pre-filled with current dw.dynamic_embeds
    # row content; on submit, persist + edit the live #how-to-* message.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-set-deposit-guide",
        description="Edit the #how-to-deposit welcome embed (title + description).",
    )
    async def set_deposit_guide_cmd(self, interaction: discord.Interaction) -> None:
        await self._open_dynamic_embed_modal(
            interaction=interaction, embed_key="how_to_deposit"
        )

    @app_commands.command(
        name="admin-set-withdraw-guide",
        description="Edit the #how-to-withdraw welcome embed (title + description).",
    )
    async def set_withdraw_guide_cmd(self, interaction: discord.Interaction) -> None:
        await self._open_dynamic_embed_modal(
            interaction=interaction, embed_key="how_to_withdraw"
        )

    async def _open_dynamic_embed_modal(
        self,
        *,
        interaction: discord.Interaction,
        embed_key: str,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        row = await bot.pool.fetchrow(
            "SELECT title, description FROM dw.dynamic_embeds WHERE embed_key = $1",
            embed_key,
        )
        if row is None:
            await interaction.response.send_message(
                f"❌ No `{embed_key}` row yet. Run `/admin-setup` first.",
                ephemeral=True,
            )
            return

        async def _on_validated(
            inner: discord.Interaction,
            payload: object,
        ) -> None:
            # ``payload`` is an EditDynamicEmbedInput; cast for clarity.
            from goldrush_core.models.dw_pydantic import EditDynamicEmbedInput

            data: EditDynamicEmbedInput = payload  # type: ignore[assignment]
            assert bot.pool is not None
            outcome = await update_dynamic_embed_content(
                pool=bot.pool,
                bot=bot,
                embed_key=embed_key,
                title=data.title,
                description=data.description,
                actor_id=inner.user.id,
            )
            await inner.response.send_message(
                f"✅ `{embed_key}` updated — {outcome.action}.",
                ephemeral=True,
            )
            await audit_config_changed(
                pool=bot.pool,
                bot=bot,
                admin_mention=inner.user.mention,
                key=f"dynamic_embeds.{embed_key}",
                new_value=f"title={data.title!r} ({len(data.description)} chars desc)",
            )
            _log.info(
                "admin_set_guide_submitted",
                actor_id=inner.user.id,
                embed_key=embed_key,
                outcome=outcome.action,
            )

        modal = EditDynamicEmbedModal(
            embed_key=embed_key,
            current_title=str(row["title"]),
            current_description=str(row["description"]),
            on_validated=_on_validated,
        )
        await interaction.response.send_modal(modal)

    # -----------------------------------------------------------------------
    # Story 10.2: /admin-set-deposit-limits, /admin-set-withdraw-limits,
    # /admin-set-fee-withdraw — UPSERT into dw.global_config + audit poster.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-set-deposit-limits",
        description="Update min / max deposit amount (gold).",
    )
    @app_commands.describe(
        min_g="Minimum deposit amount in gold (positive integer).",
        max_g="Maximum deposit amount in gold (must be >= min_g).",
    )
    async def set_deposit_limits_cmd(
        self,
        interaction: discord.Interaction,
        min_g: int,
        max_g: int,
    ) -> None:
        await self._handle_set_pair(
            interaction=interaction,
            label="deposit",
            key_min="min_deposit_g",
            key_max="max_deposit_g",
            min_value=min_g,
            max_value=max_g,
        )

    @app_commands.command(
        name="admin-set-withdraw-limits",
        description="Update min / max withdraw amount (gold).",
    )
    @app_commands.describe(
        min_g="Minimum withdraw amount in gold (positive integer).",
        max_g="Maximum withdraw amount in gold (must be >= min_g).",
    )
    async def set_withdraw_limits_cmd(
        self,
        interaction: discord.Interaction,
        min_g: int,
        max_g: int,
    ) -> None:
        await self._handle_set_pair(
            interaction=interaction,
            label="withdraw",
            key_min="min_withdraw_g",
            key_max="max_withdraw_g",
            min_value=min_g,
            max_value=max_g,
        )

    @app_commands.command(
        name="admin-set-fee-withdraw",
        description="Update the withdraw fee in basis points (200 = 2%).",
    )
    @app_commands.describe(
        bps="Fee in basis points (0-10000). 200 = 2%.",
    )
    async def set_fee_withdraw_cmd(
        self,
        interaction: discord.Interaction,
        bps: int,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        if bps < 0 or bps > 10_000:
            await interaction.response.send_message(
                f"❌ Bps must be 0-10000 (got {bps}).",
                ephemeral=True,
            )
            return
        await persist_config_int(
            bot.pool,
            key="withdraw_fee_bps",
            value=bps,
            actor_id=interaction.user.id,
        )
        await interaction.response.send_message(
            f"✅ `withdraw_fee_bps` = `{bps}` ({bps / 100:.2f}%).",
            ephemeral=True,
        )
        await audit_config_changed(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            key="withdraw_fee_bps",
            new_value=str(bps),
        )
        _log.info(
            "admin_set_fee_withdraw",
            actor_id=interaction.user.id,
            bps=bps,
        )

    # -----------------------------------------------------------------------
    # Story 10.6: /admin-treasury-balance, /admin-treasury-sweep,
    # /admin-treasury-withdraw-to-user — 2FA-gated treasury operations.
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="admin-treasury-balance",
        description="Show the bot's tracked treasury balance (gold).",
    )
    async def treasury_balance_cmd(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        # The treasury seed row is core.balances WHERE discord_id=0
        # (created by migration 0001). On first deposit nothing changes
        # there; treasury accumulates withdraw fees via dw.confirm_withdraw.
        balance = await bot.pool.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        if balance is None:
            await interaction.response.send_message(
                "❌ Treasury seed row missing — escalate to ops.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🏦 Treasury balance: **{int(balance):,}g**.\n"
            f"_Note: actual gold lives in the in-game guild bank; "
            f"this is the bot's accounting view._",
            ephemeral=True,
        )

    @app_commands.command(
        name="admin-treasury-sweep",
        description="Record a sweep of gold OUT of the treasury (2FA-gated).",
    )
    @app_commands.describe(
        amount="Amount in gold to remove from the bot's treasury accounting.",
        reason="Why the sweep happened (visible in audit log).",
    )
    async def treasury_sweep_cmd(
        self,
        interaction: discord.Interaction,
        amount: int,
        reason: str,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be a positive integer.",
                ephemeral=True,
            )
            return

        async def _on_confirm(inner: discord.Interaction) -> None:
            assert bot.pool is not None
            try:
                new_balance = await treasury_sweep(
                    bot.pool,
                    amount=amount,
                    admin_id=inner.user.id,
                    reason=reason,
                )
            except exc.InsufficientTreasury:
                await inner.response.send_message(
                    "❌ Treasury has less than the requested amount.",
                    ephemeral=True,
                )
                return
            except exc.BalanceError as e:
                await inner.response.send_message(
                    f"❌ Sweep failed: {e.message}",
                    ephemeral=True,
                )
                return
            await inner.response.send_message(
                f"✅ Swept **{amount:,}g**. New treasury balance: "
                f"**{int(new_balance):,}g**.",
                ephemeral=True,
            )
            await audit_treasury_sweep(
                pool=bot.pool,
                bot=bot,
                admin_mention=inner.user.mention,
                amount=amount,
                new_balance=int(new_balance),
                reason=reason,
            )
            _log.info(
                "admin_treasury_sweep",
                actor_id=inner.user.id,
                amount=amount,
                reason=reason,
            )

        modal = TreasurySweepConfirmModal(
            expected_amount=amount,
            on_confirm=_on_confirm,
        )
        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="admin-treasury-withdraw-to-user",
        description="Move gold from the treasury to a real user (2FA-gated).",
    )
    @app_commands.describe(
        amount="Amount in gold to send.",
        user="The recipient.",
        reason="Why (e.g., refund / dispute resolution).",
    )
    async def treasury_withdraw_to_user_cmd(
        self,
        interaction: discord.Interaction,
        amount: int,
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
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be a positive integer.",
                ephemeral=True,
            )
            return
        if user.id == 0:
            await interaction.response.send_message(
                "❌ Cannot target the treasury seed row.",
                ephemeral=True,
            )
            return

        async def _on_confirm(inner: discord.Interaction) -> None:
            assert bot.pool is not None
            try:
                await treasury_withdraw_to_user(
                    bot.pool,
                    amount=amount,
                    target_user=user.id,
                    admin_id=inner.user.id,
                    reason=reason,
                )
            except exc.InsufficientTreasury:
                await inner.response.send_message(
                    "❌ Treasury has less than the requested amount.",
                    ephemeral=True,
                )
                return
            except exc.BalanceError as e:
                await inner.response.send_message(
                    f"❌ Withdraw failed: {e.message}",
                    ephemeral=True,
                )
                return
            await inner.response.send_message(
                f"✅ Sent **{amount:,}g** from treasury to {user.mention}.",
                ephemeral=True,
            )
            await audit_treasury_withdraw_to_user(
                pool=bot.pool,
                bot=bot,
                admin_mention=inner.user.mention,
                target_mention=user.mention,
                amount=amount,
                reason=reason,
            )
            _log.info(
                "admin_treasury_withdraw_to_user",
                actor_id=inner.user.id,
                target_id=user.id,
                amount=amount,
                reason=reason,
            )

        modal = TreasuryWithdrawConfirmModal(
            expected_amount=amount,
            expected_user_id=user.id,
            on_confirm=_on_confirm,
        )
        await interaction.response.send_modal(modal)

    async def _handle_set_pair(
        self,
        *,
        interaction: discord.Interaction,
        label: str,
        key_min: str,
        key_max: str,
        min_value: int,
        max_value: int,
    ) -> None:
        """Shared logic for set-deposit-limits / set-withdraw-limits."""
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        if min_value <= 0 or max_value <= 0:
            await interaction.response.send_message(
                "❌ Both `min_g` and `max_g` must be positive.",
                ephemeral=True,
            )
            return
        if min_value > max_value:
            await interaction.response.send_message(
                f"❌ `min_g` ({min_value:,}) must be <= `max_g` ({max_value:,}).",
                ephemeral=True,
            )
            return
        await persist_config_int(
            bot.pool,
            key=key_min,
            value=min_value,
            actor_id=interaction.user.id,
        )
        await persist_config_int(
            bot.pool,
            key=key_max,
            value=max_value,
            actor_id=interaction.user.id,
        )
        await interaction.response.send_message(
            f"✅ `{key_min}` = `{min_value:,}g`, `{key_max}` = `{max_value:,}g`.",
            ephemeral=True,
        )
        # One audit row per key for forensic clarity.
        await audit_config_changed(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            key=key_min,
            new_value=f"{min_value:,}g",
        )
        await audit_config_changed(
            pool=bot.pool,
            bot=bot,
            admin_mention=interaction.user.mention,
            key=key_max,
            new_value=f"{max_value:,}g",
        )
        _log.info(
            f"admin_set_{label}_limits",
            actor_id=interaction.user.id,
            min_g=min_value,
            max_g=max_value,
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
