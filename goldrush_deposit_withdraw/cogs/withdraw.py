"""Withdraw cog — the user-facing ``/withdraw`` slash command.

Stories 6.1 + 6.2 land the open-flow end-to-end (mirror of the
deposit flow with the fee breakdown surfaced in the open embed):

1. ``/withdraw`` opens ``WithdrawModal``.
2. On valid submit, the bot creates a private thread under
   ``#withdraw``, calls ``dw.create_withdraw_ticket`` (which
   ALSO locks the balance and captures the fee at creation time),
   and posts the ``withdraw_ticket_open_embed`` showing
   ``amount`` / ``fee`` / ``amount_delivered`` plus a
   ``@cashier`` mention message.
3. Failure paths roll back the thread and surface an ephemeral.

The withdraw failure surface is wider than deposit because the
SECURITY DEFINER fn also surfaces ``user_not_registered`` (no
``core.users`` row yet — the user has never deposited) and
``insufficient_balance``. Each is mapped to a friendly message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from goldrush_core.discord_helpers.channel_binding import resolve_channel_id
from goldrush_core.discord_helpers.role_binding import role_mention
from goldrush_core.embeds.dw_tickets import withdraw_ticket_open_embed
from goldrush_core.models.dw_pydantic import WithdrawModalInput

from goldrush_deposit_withdraw.cashiers.alert import post_cashier_alert
from goldrush_deposit_withdraw.tickets.factory import create_ticket_thread
from goldrush_deposit_withdraw.tickets.orchestration import (
    WithdrawOutcome,
    open_withdraw_ticket,
)
from goldrush_deposit_withdraw.views.modals import WithdrawModal

if TYPE_CHECKING:
    from goldrush_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


class WithdrawCog(commands.Cog):
    """Hosts the ``/withdraw`` slash command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="withdraw",
        description="Open a withdraw ticket — convert bot balance back to in-game gold.",
    )
    async def withdraw(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        configured = await resolve_channel_id(bot.pool, "withdraw")
        if configured is None:
            await interaction.response.send_message(
                "Withdraw channel not configured yet — ask an admin to run "
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

        if not bot.rate_limiters["withdraw_create"].acquire(interaction.user.id):
            await interaction.response.send_message(
                "You opened a ticket too recently. Wait a minute and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            WithdrawModal(on_validated=self._on_modal_validated)
        )

    async def _on_modal_validated(
        self,
        interaction: discord.Interaction,
        payload: WithdrawModalInput,
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        assert bot.pool is not None

        parent = interaction.channel
        if not isinstance(parent, discord.TextChannel):
            await interaction.response.send_message(
                "This command must be run in a server text channel.",
                ephemeral=True,
            )
            return

        thread = await create_ticket_thread(
            parent=parent,
            name=f"withdraw-pending-{interaction.user.id}",
            user=interaction.user,
            reason="GoldRush withdraw ticket — pre-DB",
        )

        outcome = await open_withdraw_ticket(
            pool=bot.pool,
            payload=payload,
            discord_id=interaction.user.id,
            thread_id=thread.id,
            parent_channel_id=parent.id,
        )

        if isinstance(outcome, WithdrawOutcome.Success):
            await thread.edit(name=outcome.ticket_uid)
            # Read the freshly-created row to pull out the fee that
            # the SECURITY DEFINER fn captured. Spec §4.2: the fee
            # is fixed at creation time so subsequent rate changes
            # don't affect open tickets.
            fee, delivered = await _fetch_fee_and_delivered(
                bot.pool, outcome.ticket_uid, payload.amount
            )
            embed = withdraw_ticket_open_embed(
                ticket_uid=outcome.ticket_uid,
                char_name=payload.char_name,
                region=payload.region,
                faction=payload.faction,
                amount=payload.amount,
                fee=fee,
                amount_delivered=delivered,
                created_at=discord.utils.utcnow(),
            )
            await thread.send(embed=embed)
            cashier_ping = await role_mention(bot.pool, "cashier")
            await thread.send(
                f"{cashier_ping} — new withdraw ticket. Run `/claim` to take it.",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            await interaction.response.send_message(
                f"Ticket opened: {thread.mention}",
                ephemeral=True,
            )
            # Story 5.3 (also covers withdraw): post alert in
            # #cashier-alerts. Same poster as deposit.
            await post_cashier_alert(
                pool=bot.pool,
                bot=bot,
                ticket_uid=outcome.ticket_uid,
                ticket_type="withdraw",
                region=payload.region,
                faction=payload.faction,
                amount=payload.amount,
                ticket_channel_mention=thread.mention,
            )
            _log.info(
                "withdraw_ticket_opened",
                ticket_uid=outcome.ticket_uid,
                discord_id=interaction.user.id,
                amount=payload.amount,
                fee=fee,
                amount_delivered=delivered,
            )
            return

        await _safe_delete_thread(thread)
        await interaction.response.send_message(
            _format_withdraw_failure(outcome),
            ephemeral=True,
        )
        _log.warning(
            "withdraw_ticket_rejected",
            outcome=type(outcome).__name__,
            discord_id=interaction.user.id,
        )


async def _fetch_fee_and_delivered(
    pool: object, ticket_uid: str, amount: int
) -> tuple[int, int]:
    """Read the captured fee from ``dw.withdraw_tickets`` post-insert.

    The fee is a function of ``dw.global_config.withdraw_fee_bps`` at
    creation time; reading it back avoids a duplicate computation.
    On any read failure we fall back to a 2 % approximation so the
    user sees SOMETHING reasonable (the canonical value will be
    displayed in subsequent message edits).
    """
    try:
        row = await pool.fetchrow(  # type: ignore[attr-defined]
            "SELECT fee FROM dw.withdraw_tickets WHERE ticket_uid = $1",
            ticket_uid,
        )
        if row is not None and row["fee"] is not None:
            fee = int(row["fee"])
            return fee, amount - fee
    except Exception as e:
        _log.warning("withdraw_fee_lookup_failed", ticket_uid=ticket_uid, error=str(e))
    fallback_fee = amount * 200 // 10000  # 2 % default per spec
    return fallback_fee, amount - fallback_fee


def _format_withdraw_failure(outcome: object) -> str:
    """Render a withdraw-ticket failure outcome as an ephemeral message."""
    if isinstance(outcome, WithdrawOutcome.UserBanned):
        return (
            "❌ You are blacklisted from withdrawing. Open a dispute "
            "via the support channel if you believe this is in error."
        )
    if isinstance(outcome, WithdrawOutcome.UserNotRegistered):
        return (
            "❌ You don't have a balance yet — make a deposit first via "
            "`/deposit`."
        )
    if isinstance(outcome, WithdrawOutcome.InsufficientBalance):
        return f"❌ Insufficient balance: {outcome.message}"
    if isinstance(outcome, WithdrawOutcome.AmountOutOfRange):
        return f"❌ Amount out of range: {outcome.message}"
    if isinstance(outcome, WithdrawOutcome.InvalidInput):
        return f"❌ Invalid input: {outcome.message}"
    if isinstance(outcome, WithdrawOutcome.ConfigError):
        return (
            "❌ The bot's config is incomplete — admins should review "
            "`dw.global_config`. Try again later."
        )
    return "❌ Could not open the withdraw ticket. Try again later."


async def _safe_delete_thread(thread: discord.Thread) -> None:
    try:
        await thread.delete()
    except Exception as e:
        _log.warning("thread_delete_failed", thread_id=thread.id, error=str(e))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WithdrawCog(bot))
