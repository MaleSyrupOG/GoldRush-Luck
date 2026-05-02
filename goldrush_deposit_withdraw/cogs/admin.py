"""Admin cog — every ``/admin *`` command (spec §5.1).

Story 4.2 lands the skeleton; the admin commands themselves
(``/admin setup``, treasury sweeps, ban/unban, dispute resolution,
etc.) land across Stories 9 and 10.
"""

from __future__ import annotations

from discord.ext import commands


class AdminCog(commands.Cog):
    """Privileged commands restricted to the ``@admin`` role.

    Every command in this cog carries the
    ``@app_commands.default_permissions()`` decorator (so they are
    hidden in autocomplete) plus a ``@require_role("admin")`` runtime
    check (per spec §5.1). Story 4.2 holds the cog skeleton; the
    decorators land alongside each command.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
