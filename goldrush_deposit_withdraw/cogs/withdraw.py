"""Withdraw cog — the user-facing ``/withdraw`` slash command.

Story 4.2 lands the skeleton; Story 6 adds the slash command +
``WithdrawModal`` flow (which validates balance via the SECURITY
DEFINER ``dw.create_withdraw_ticket`` and locks the requested
amount on the user's balance row).
"""

from __future__ import annotations

from discord.ext import commands


class WithdrawCog(commands.Cog):
    """User-facing withdraw commands.

    Restricted to the ``#withdraw`` channel via ``@require_channel``
    (added alongside the actual command in Story 6).
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WithdrawCog(bot))
