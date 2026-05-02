"""Account cog — ``/balance`` and ``/help`` commands.

Story 4.2 lands the skeleton; Story 4.3 adds the actual slash
commands and their handlers.
"""

from __future__ import annotations

from discord.ext import commands


class AccountCog(commands.Cog):
    """User-facing account commands.

    Holds a reference to the bot so handlers can reach ``bot.pool``
    when querying ``core.balances`` for ``/balance``.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AccountCog(bot))
