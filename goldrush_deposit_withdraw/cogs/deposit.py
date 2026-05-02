"""Deposit cog — the user-facing ``/deposit`` slash command.

Story 4.2 lands the skeleton; Story 5 adds the slash command +
``DepositModal`` flow, channel-binding to ``#deposit``, and the
ticket-creation orchestration.
"""

from __future__ import annotations

from discord.ext import commands


class DepositCog(commands.Cog):
    """User-facing deposit commands.

    Restricted to the ``#deposit`` channel via the ``@require_channel``
    decorator (added alongside the actual command in Story 5).
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DepositCog(bot))
