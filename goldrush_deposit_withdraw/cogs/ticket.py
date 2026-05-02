"""Ticket cog — in-channel slash commands for an open ticket.

These are the ``/claim``, ``/release``, ``/cancel``, ``/cancel-mine``
and ``/confirm`` commands that fire INSIDE a ticket channel (the
private channel created when a deposit or withdraw is opened).

Story 4.2 lands the skeleton; the commands themselves land in
Stories 5 (deposit) and 6 (withdraw).
"""

from __future__ import annotations

from discord.ext import commands


class TicketCog(commands.Cog):
    """Commands run inside a deposit/withdraw ticket channel.

    The ``@require_channel`` decorator binds them to the ticket
    channel only — running ``/claim`` from a public channel returns
    a friendly ephemeral error.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))
