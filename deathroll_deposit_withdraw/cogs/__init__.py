"""Cog manifest for the Deposit/Withdraw bot.

Six cogs map to the six command families in spec §5.1:

- ``account``  — ``/balance``, ``/help``                  (Story 4.3)
- ``admin``    — every ``/admin *`` command               (Stories 9 / 10)
- ``cashier``  — every ``/cashier *`` command             (Story 7)
- ``deposit``  — ``/deposit`` user-side                   (Story 5)
- ``ticket``   — ``/claim``, ``/release``, ``/cancel``,
                  ``/confirm`` inside ticket channels     (Stories 5 / 6)
- ``withdraw`` — ``/withdraw`` user-side                  (Story 6)

Every cog module exposes an ``async def setup(bot)`` function — the
contract ``discord.py`` enforces for ``Bot.load_extension``. Story
4.2 lands the skeletons (empty ``Cog`` subclass + ``setup``) so the
extension manifest is complete; subsequent stories fill in the
slash commands, listeners, and views.
"""

from __future__ import annotations
