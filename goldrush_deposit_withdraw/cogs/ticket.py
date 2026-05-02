"""Ticket-thread commands — ``/claim``, ``/release``, ``/cancel``, ``/cancel-mine``.

Stories 5.4 + 6.3 land the lifecycle commands cashiers (and ticket
owners) run INSIDE a deposit / withdraw ticket thread. The commands
look up the (ticket_type, ticket_uid) by the current thread's id —
both ``dw.deposit_tickets`` and ``dw.withdraw_tickets`` carry a
``thread_id`` column that uniquely identifies a ticket.

Behaviour:

- ``/claim``       — cashier action. Opens the ticket to a specific
                     cashier (region match enforced by the
                     SECURITY DEFINER fn). Posts the
                     ``*_ticket_claimed_embed`` on success.
- ``/release``     — claimed-by-me action. Hands the ticket back to
                     ``open`` so a different cashier can claim.
- ``/cancel``      — claimed-by-me action with a reason. Withdraw
                     cancel ALSO refunds the locked balance (handled
                     in the SECURITY DEFINER fn).
- ``/cancel-mine`` — ticket-owner action. Only valid before any
                     cashier has claimed (status='open'); we let
                     the SECURITY DEFINER fn enforce the state
                     check rather than trying to short-circuit
                     here.

Each command posts a state-change embed in the thread on success
and an ephemeral on failure. Editing the original open embed in
place is left for a follow-up — the per-state messages are good
UX in the meantime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from goldrush_core.embeds.dw_tickets import (
    deposit_ticket_cancelled_embed,
    deposit_ticket_claimed_embed,
    withdraw_ticket_cancelled_embed,
    withdraw_ticket_claimed_embed,
)

from goldrush_deposit_withdraw.tickets.orchestration import (
    LifecycleOutcome,
    cancel_ticket_dispatch,
    claim_ticket_for_cashier,
    release_ticket_by_cashier,
)

if TYPE_CHECKING:
    from goldrush_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


_TicketType = Literal["deposit", "withdraw"]


class TicketCog(commands.Cog):
    """Slash commands run inside a ticket thread."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="claim",
        description="Claim this open ticket as the cashier.",
    )
    async def claim(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        ctx = await _resolve_ticket_context(bot.pool, interaction)
        if ctx is None:
            return

        ticket_type, ticket_uid, ticket_row = ctx
        outcome = await claim_ticket_for_cashier(
            pool=bot.pool,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            cashier_id=interaction.user.id,
        )

        if isinstance(outcome, LifecycleOutcome.Success):
            embed = _build_claimed_embed(
                ticket_type=ticket_type,
                ticket_row=ticket_row,
                cashier_mention=interaction.user.mention,
            )
            assert isinstance(interaction.channel, discord.Thread | discord.TextChannel)
            await interaction.channel.send(embed=embed)
            await interaction.response.send_message(
                "Claimed. Coordinate the in-game trade with the user here.",
                ephemeral=True,
            )
            _log.info(
                "ticket_claimed",
                ticket_uid=ticket_uid,
                ticket_type=ticket_type,
                cashier_id=interaction.user.id,
            )
            return

        await interaction.response.send_message(
            _format_lifecycle_failure(outcome, action="claim"),
            ephemeral=True,
        )

    @app_commands.command(
        name="release",
        description="Release a ticket you previously claimed back to open.",
    )
    async def release(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        ctx = await _resolve_ticket_context(bot.pool, interaction)
        if ctx is None:
            return
        ticket_type, ticket_uid, _ = ctx

        outcome = await release_ticket_by_cashier(
            pool=bot.pool,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            cashier_id=interaction.user.id,
        )
        if isinstance(outcome, LifecycleOutcome.Success):
            await interaction.response.send_message(
                "Released. The ticket is open again for any cashier to claim.",
                ephemeral=False,
            )
            _log.info(
                "ticket_released",
                ticket_uid=ticket_uid,
                ticket_type=ticket_type,
                cashier_id=interaction.user.id,
            )
            return

        await interaction.response.send_message(
            _format_lifecycle_failure(outcome, action="release"),
            ephemeral=True,
        )

    @app_commands.command(
        name="cancel",
        description="Cancel this ticket (cashier action; provide a reason).",
    )
    @app_commands.describe(reason="Why you are cancelling (visible in audit log).")
    async def cancel(
        self, interaction: discord.Interaction, reason: str
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        ctx = await _resolve_ticket_context(bot.pool, interaction)
        if ctx is None:
            return
        ticket_type, ticket_uid, ticket_row = ctx

        outcome = await cancel_ticket_dispatch(
            pool=bot.pool,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            actor_id=interaction.user.id,
            reason=reason,
        )
        if isinstance(outcome, LifecycleOutcome.Success):
            embed = _build_cancelled_embed(
                ticket_type=ticket_type,
                ticket_row=ticket_row,
                reason=reason,
            )
            assert isinstance(interaction.channel, discord.Thread | discord.TextChannel)
            await interaction.channel.send(embed=embed)
            await interaction.response.send_message(
                "Ticket cancelled.",
                ephemeral=True,
            )
            _log.info(
                "ticket_cancelled",
                ticket_uid=ticket_uid,
                ticket_type=ticket_type,
                actor_id=interaction.user.id,
                reason=reason,
            )
            return

        await interaction.response.send_message(
            _format_lifecycle_failure(outcome, action="cancel"),
            ephemeral=True,
        )

    @app_commands.command(
        name="cancel-mine",
        description="Cancel your own open ticket before a cashier claims it.",
    )
    async def cancel_mine(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        ctx = await _resolve_ticket_context(bot.pool, interaction)
        if ctx is None:
            return
        ticket_type, ticket_uid, ticket_row = ctx

        if cast(int, ticket_row["discord_id"]) != interaction.user.id:
            await interaction.response.send_message(
                "Only the ticket owner can use `/cancel-mine`.",
                ephemeral=True,
            )
            return
        if ticket_row["status"] != "open":
            await interaction.response.send_message(
                "Your ticket is no longer open — ask the claiming cashier "
                "to `/cancel` instead.",
                ephemeral=True,
            )
            return

        outcome = await cancel_ticket_dispatch(
            pool=bot.pool,
            ticket_type=ticket_type,
            ticket_uid=ticket_uid,
            actor_id=interaction.user.id,
            reason="cancel-mine (user)",
        )
        if isinstance(outcome, LifecycleOutcome.Success):
            embed = _build_cancelled_embed(
                ticket_type=ticket_type,
                ticket_row=ticket_row,
                reason="cancelled by ticket owner",
            )
            assert isinstance(interaction.channel, discord.Thread | discord.TextChannel)
            await interaction.channel.send(embed=embed)
            await interaction.response.send_message(
                "Your ticket has been cancelled.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            _format_lifecycle_failure(outcome, action="cancel-mine"),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_ticket_context(
    pool: object, interaction: discord.Interaction
) -> tuple[_TicketType, str, dict[str, object]] | None:
    """Look up the ticket the thread is associated with.

    Returns ``(ticket_type, ticket_uid, row)`` or ``None`` if the
    interaction's channel does not match any ticket. ``None`` ALSO
    short-circuits the response with an ephemeral so the caller
    (the slash command body) can simply ``return`` after it.
    """
    if interaction.channel is None or interaction.channel_id is None:
        await interaction.response.send_message(
            "Run this command inside a ticket thread.",
            ephemeral=True,
        )
        return None

    row = await pool.fetchrow(  # type: ignore[attr-defined]
        """
        SELECT 'deposit' AS ticket_type, ticket_uid, discord_id, status,
               char_name, realm, region, faction, amount
          FROM dw.deposit_tickets WHERE thread_id = $1
        UNION ALL
        SELECT 'withdraw' AS ticket_type, ticket_uid, discord_id, status,
               char_name, realm, region, faction, amount
          FROM dw.withdraw_tickets WHERE thread_id = $1
        LIMIT 1
        """,
        interaction.channel_id,
    )
    if row is None:
        await interaction.response.send_message(
            "This channel is not bound to any open ticket.",
            ephemeral=True,
        )
        return None

    return (
        str(row["ticket_type"]),  # type: ignore[return-value]
        str(row["ticket_uid"]),
        dict(row),
    )


def _build_claimed_embed(
    *,
    ticket_type: _TicketType,
    ticket_row: dict[str, object],
    cashier_mention: str,
) -> discord.Embed:
    """Render the post-claim embed.

    Cashier-side data (the cashier's char + realm + ingame location)
    isn't available here without a separate query / interaction —
    Story 7.x's ``/cashier set-status online`` will surface that
    via ``dw.cashier_status``. Until then we fill placeholders.
    """
    amount = cast(int, ticket_row["amount"])
    common = dict(
        ticket_uid=str(ticket_row["ticket_uid"]),
        amount=amount,
        user_char_name=str(ticket_row["char_name"]),
        cashier_mention=cashier_mention,
        cashier_char="(set via /cashier)",
        cashier_realm="(see cashier)",
        cashier_region=str(ticket_row["region"]),
        location="(cashier will share in this thread)",
    )
    if ticket_type == "deposit":
        return deposit_ticket_claimed_embed(**common)  # type: ignore[arg-type]
    return withdraw_ticket_claimed_embed(
        amount_delivered=amount,
        **common,  # type: ignore[arg-type]
    )


def _build_cancelled_embed(
    *,
    ticket_type: _TicketType,
    ticket_row: dict[str, object],
    reason: str,
) -> discord.Embed:
    cancelled_at = discord.utils.utcnow()
    if ticket_type == "deposit":
        return deposit_ticket_cancelled_embed(
            ticket_uid=str(ticket_row["ticket_uid"]),
            reason=reason,
            cancelled_at=cancelled_at,
        )
    # Withdraw cancel refunds the locked amount per migration 0007.
    refunded = cast(int, ticket_row["amount"])
    return withdraw_ticket_cancelled_embed(
        ticket_uid=str(ticket_row["ticket_uid"]),
        refunded_amount=refunded,
        reason=reason,
        cancelled_at=cancelled_at,
    )


def _format_lifecycle_failure(outcome: object, *, action: str) -> str:
    if isinstance(outcome, LifecycleOutcome.TicketNotFound):
        return f"❌ Cannot {action}: ticket not found."
    if isinstance(outcome, LifecycleOutcome.AlreadyClaimed):
        return (
            "❌ This ticket is already claimed by another cashier. "
            "If they're inactive, ask an admin or wait for the auto-release."
        )
    if isinstance(outcome, LifecycleOutcome.RegionMismatch):
        return (
            "❌ You don't have an active char in this ticket's region. "
            "Use `/cashier addchar` first."
        )
    if isinstance(outcome, LifecycleOutcome.NotClaimed):
        return f"❌ Cannot {action}: ticket is not claimed."
    if isinstance(outcome, LifecycleOutcome.WrongCashier):
        return f"❌ Only the claiming cashier can {action} this ticket."
    if isinstance(outcome, LifecycleOutcome.AlreadyTerminal):
        return f"❌ Ticket is already in a terminal state — can't {action} it."
    return f"❌ Could not {action} the ticket. Try again later."


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))
