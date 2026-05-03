"""Per-ticket thread (or channel) creation helper.

Spec §5.4 says the bot creates a private thread under the
``#deposit`` / ``#withdraw`` parent for each ticket. The user is
added explicitly; ``@cashier`` role members see the thread because
they have ``manage_threads`` on the parent (Story 3.4 channel
factory wires that in).

Reality note: the live server uses private CHANNELS, not threads
(captured in ``reference_actual_server_state.md``). We follow the
spec literally here because the AC is explicit; the v1.1 spec bump
will swap in channel creation if Aleix decides to align.

The factory is a thin async wrapper. It exists so:

- Tests can mock the Discord API without monkey-patching every
  call site.
- Future changes (channel vs thread, auto-archive duration tuning)
  happen in one place.
"""

from __future__ import annotations

from typing import Any, Literal

import discord

# Discord enforces this enumeration for auto-archive duration; typing
# with Literal makes a typo or out-of-range value fail at type-check.
AutoArchiveMinutes = Literal[60, 1440, 4320, 10080]
_DEFAULT_AUTO_ARCHIVE_MINUTES: AutoArchiveMinutes = 1440  # 24-hour backstop


async def create_ticket_thread(
    *,
    parent: discord.TextChannel,
    name: str,
    user: discord.Member | discord.User,
    auto_archive_duration: AutoArchiveMinutes = _DEFAULT_AUTO_ARCHIVE_MINUTES,
    reason: str = "DeathRoll ticket",
) -> Any:
    """Create a private thread under ``parent`` and add ``user`` to it.

    Returns the created thread (typed ``Any`` because in tests we
    pass a fake; the real return is ``discord.Thread``).

    Raises:
        ValueError: when ``name`` is empty or longer than 100 chars
            (Discord's hard limit). Surfacing as a typed exception
            here turns a future programming bug into an obvious
            stack trace at the call site rather than an opaque HTTP
            400 from Discord.
    """
    if not name or len(name) > 100:
        raise ValueError(f"thread name must be 1-100 chars, got {len(name)}")

    thread = await parent.create_thread(
        name=name,
        type=discord.ChannelType.private_thread,
        invitable=False,
        auto_archive_duration=auto_archive_duration,
        reason=reason,
    )
    await thread.add_user(user)
    return thread


__all__ = ["create_ticket_thread"]
