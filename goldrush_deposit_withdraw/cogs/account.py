"""Account cog — ``/balance`` and ``/help`` (Story 4.3).

Both commands are ephemeral: the response is visible only to the
invoker. ``/balance`` queries ``core.balances`` + ``dw.deposit_tickets``
+ ``dw.withdraw_tickets`` via ``fetch_account_stats`` and renders
``account_summary_embed``; users with no ``core.users`` row get
``no_balance_embed`` redirecting them to ``#how-to-deposit``.
``/help`` accepts an optional ``topic`` argument (deposit / withdraw /
fairness / support) and renders the matching topic page.

The cog reaches the DB pool through ``self.bot.pool``. Tests in
``tests/unit/dw/test_account_cog.py`` exercise only the structural
contract (commands registered with the right names and parameter
shapes); end-to-end interaction tests land in Epic 14.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from goldrush_core.balance.account_stats import fetch_account_stats
from goldrush_core.embeds.account import (
    HELP_TOPICS,
    account_summary_embed,
    help_embed,
    no_balance_embed,
)

if TYPE_CHECKING:
    from goldrush_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


# Pre-built choices list so Discord renders an autocomplete dropdown
# rather than a free-text field — better UX, fewer mistyped topics.
_HELP_TOPIC_CHOICES = [
    app_commands.Choice(name=key, value=key) for key in HELP_TOPICS
]


class AccountCog(commands.Cog):
    """User-facing account commands for the D/W bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="balance", description="Show your GoldRush balance and lifetime totals.")
    async def balance(self, interaction: discord.Interaction) -> None:
        """Render the ephemeral balance embed.

        Looks up the user via ``fetch_account_stats``; falls back to
        ``no_balance_embed`` (redirecting to ``#how-to-deposit``) when
        the user has no ``core.users`` row yet.
        """
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            # Defensive: if setup_hook hasn't completed (shouldn't be
            # possible after on_ready) we surface a friendly error
            # rather than a stack trace in the user's chat.
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return

        stats = await fetch_account_stats(bot.pool, discord_id=interaction.user.id)
        if stats is None:
            mention = _resolve_how_to_deposit_mention(bot)
            embed = no_balance_embed(deposit_channel_mention=mention)
        else:
            embed = account_summary_embed(
                balance=stats.balance,
                total_deposited=stats.total_deposited,
                total_withdrawn=stats.total_withdrawn,
                lifetime_fee_paid=stats.lifetime_fee_paid,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        _log.info(
            "balance_rendered",
            user_id=interaction.user.id,
            registered=stats is not None,
        )

    @app_commands.command(name="help", description="Show help for a topic (deposit, withdraw, fairness, support).")
    @app_commands.describe(topic="Pick a topic, or omit for the topic list.")
    @app_commands.choices(topic=_HELP_TOPIC_CHOICES)
    async def help(
        self,
        interaction: discord.Interaction,
        topic: app_commands.Choice[str] | None = None,
    ) -> None:
        """Render the ``/help`` embed for the chosen topic, or the topic list."""
        topic_key = topic.value if topic is not None else None
        embed = help_embed(topic=topic_key)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _resolve_how_to_deposit_mention(bot: commands.Bot) -> str:
    """Return ``<#channel_id>`` for ``#how-to-deposit`` or the literal name.

    Once Story 3.4's channel factory has run, the channel id is
    persisted in ``dw.global_config``. Until that integration lands
    (Story 10.1 wraps it under ``/admin setup``), we fall back to
    the literal channel name — Discord still renders it as plain
    text but the user can search for the channel.
    """
    # Try a name-based lookup as a best-effort. Real implementation
    # will read from dw.global_config in Story 10.x.
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == "how-to-deposit":
                return channel.mention
    return "#how-to-deposit"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AccountCog(bot))
