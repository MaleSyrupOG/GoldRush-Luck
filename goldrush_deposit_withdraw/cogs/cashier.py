"""Cashier cog — every ``/cashier *`` command (spec §5.1).

Story 4.2 lands the skeleton; the cashier commands (``addchar``,
``removechar``, ``listchars``, ``set-status``, ``mystats``) land in
Story 7.
"""

from __future__ import annotations

from discord.ext import commands


class CashierCog(commands.Cog):
    """Commands restricted to the ``@cashier`` role.

    Visible to ``@cashier`` and ``@admin`` only; the role check is
    applied at the runtime layer once the commands themselves land.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CashierCog(bot))
