"""Deposit cog — the user-facing ``/deposit`` slash command.

Stories 5.1 + 5.2 land the open-flow end-to-end:

1. ``/deposit`` opens ``DepositModal``.
2. On valid submit, the bot creates a private thread under
   ``#deposit``, calls ``dw.create_deposit_ticket`` with the
   thread id, and posts the ``deposit_ticket_open_embed`` plus a
   ``@cashier`` mention message inside the thread.
3. The user sees an ephemeral confirmation pointing at the thread.
4. Story 5.3 adds the ``cashier_alert_embed`` posted in
   ``#cashier-alerts``.

Failure paths (banned, range, config, unexpected) destroy the
just-created thread and surface an ephemeral error to the user.
Rate-limit denial (1 ticket per 60 s) is enforced before the
thread is created so a tight loop can't litter empty threads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from deathroll_core.discord_helpers.channel_binding import resolve_channel_id
from deathroll_core.discord_helpers.role_binding import role_mention
from deathroll_core.embeds.dw_tickets import deposit_ticket_open_embed
from deathroll_core.models.dw_pydantic import DepositModalInput

from deathroll_deposit_withdraw.audit_log import audit_ticket_opened
from deathroll_deposit_withdraw.cashiers.alert import post_cashier_alert
from deathroll_deposit_withdraw.tickets.factory import create_ticket_thread
from deathroll_deposit_withdraw.tickets.orchestration import (
    DepositOutcome,
    open_deposit_ticket,
)
from deathroll_deposit_withdraw.views.modals import DepositModal

if TYPE_CHECKING:
    from deathroll_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


class DepositCog(commands.Cog):
    """Hosts the ``/deposit`` slash command.

    The command is restricted to the ``#deposit`` channel — the
    canonical id is read from ``dw.global_config.channel_id_deposit``
    at invocation time so re-binding via ``/admin set-channel``
    propagates without restart.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="deposit",
        description="Open a deposit ticket — convert in-game gold into bot balance.",
    )
    async def deposit(self, interaction: discord.Interaction) -> None:
        """Entry point. Validates context, opens the modal."""
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        # Channel binding: must be in the configured #deposit channel.
        configured = await resolve_channel_id(bot.pool, "deposit")
        if configured is None:
            await interaction.response.send_message(
                "Deposit channel not configured yet — ask an admin to run "
                "`/admin setup`.",
                ephemeral=True,
            )
            return
        if interaction.channel_id != configured:
            await interaction.response.send_message(
                f"Use this command in <#{configured}>.",
                ephemeral=True,
            )
            return

        # Rate limit: 1 ticket per user per 60 s.
        if not bot.rate_limiters["deposit_create"].acquire(interaction.user.id):
            await interaction.response.send_message(
                "You opened a ticket too recently. Wait a minute and try again.",
                ephemeral=True,
            )
            return

        # Open the modal. ``on_validated`` runs after pydantic accepts
        # the user's input.
        await interaction.response.send_modal(
            DepositModal(on_validated=self._on_modal_validated)
        )

    async def _on_modal_validated(
        self,
        interaction: discord.Interaction,
        payload: DepositModalInput,
    ) -> None:
        """Run after the user submits a syntactically valid modal.

        Creates a private thread, calls the SECURITY DEFINER fn,
        and posts the open embed + cashier mention. Failure paths
        tear the thread back down so the channel doesn't accumulate
        empty containers.
        """
        bot: DwBot = self.bot  # type: ignore[assignment]
        assert bot.pool is not None

        parent = interaction.channel
        if not isinstance(parent, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be run in a server text channel.",
                ephemeral=True,
            )
            return

        # We don't yet know the ticket UID — name the thread
        # generically and rename it after the SECURITY DEFINER
        # returns. ``deposit-pending`` is intentional so a half-
        # created thread is recognisable in the audit log.
        thread = await create_ticket_thread(
            parent=parent,
            name=f"deposit-pending-{interaction.user.id}",
            user=interaction.user,
            reason="DeathRoll deposit ticket — pre-DB",
        )

        outcome = await open_deposit_ticket(
            pool=bot.pool,
            payload=payload,
            discord_id=interaction.user.id,
            thread_id=thread.id,
            parent_channel_id=parent.id,
        )

        if isinstance(outcome, DepositOutcome.Success):
            # Rename the thread to the canonical ticket UID and post
            # the open embed + cashier mention.
            await thread.edit(name=outcome.ticket_uid)
            embed = deposit_ticket_open_embed(
                ticket_uid=outcome.ticket_uid,
                char_name=payload.char_name,
                region=payload.region,
                faction=payload.faction,
                amount=payload.amount,
                created_at=discord.utils.utcnow(),
            )
            await thread.send(embed=embed)
            cashier_ping = await role_mention(bot.pool, "cashier")
            await thread.send(
                f"{cashier_ping} — new deposit ticket. Run `/claim` to take it.",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            await interaction.response.send_message(
                f"Ticket opened: {thread.mention}",
                ephemeral=True,
            )
            # Story 5.3: post the alert in #cashier-alerts. Best-effort
            # — failure here doesn't roll back the ticket.
            await post_cashier_alert(
                pool=bot.pool,
                bot=bot,
                ticket_uid=outcome.ticket_uid,
                ticket_type="deposit",
                region=payload.region,
                faction=payload.faction,
                amount=payload.amount,
                ticket_channel_mention=thread.mention,
            )
            await audit_ticket_opened(
                pool=bot.pool,
                bot=bot,
                ticket_type="deposit",
                ticket_uid=outcome.ticket_uid,
                user_mention=interaction.user.mention,
                amount=payload.amount,
            )
            _log.info(
                "deposit_ticket_opened",
                ticket_uid=outcome.ticket_uid,
                discord_id=interaction.user.id,
                amount=payload.amount,
            )
            return

        # Failure paths: tear the thread down and surface an ephemeral.
        await _safe_delete_thread(thread)
        await interaction.response.send_message(
            _format_deposit_failure(outcome),
            ephemeral=True,
        )
        _log.warning(
            "deposit_ticket_rejected",
            outcome=type(outcome).__name__,
            discord_id=interaction.user.id,
        )


def _format_deposit_failure(outcome: object) -> str:
    """Render a deposit-ticket failure outcome as an ephemeral message."""
    if isinstance(outcome, DepositOutcome.UserBanned):
        return (
            "❌ You are blacklisted from creating deposit tickets. "
            "Open a dispute via the support channel if you believe this is in error."
        )
    if isinstance(outcome, DepositOutcome.AmountOutOfRange):
        return f"❌ Amount out of range: {outcome.message}"
    if isinstance(outcome, DepositOutcome.InvalidInput):
        return f"❌ Invalid input: {outcome.message}"
    if isinstance(outcome, DepositOutcome.ConfigError):
        return (
            "❌ The bot's config is incomplete — admins should review "
            "`dw.global_config`. Try again later."
        )
    return "❌ Could not open the deposit ticket. Try again later."


async def _safe_delete_thread(thread: discord.Thread) -> None:
    """Best-effort thread teardown.

    A network blip during the DB call could leave a half-created
    thread behind; this helper deletes it without ever raising
    (logging only). Story 8 (cleanup worker) sweeps for orphan
    threads as a backstop.
    """
    try:
        await thread.delete()
    except Exception as e:
        _log.warning("thread_delete_failed", thread_id=thread.id, error=str(e))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DepositCog(bot))
