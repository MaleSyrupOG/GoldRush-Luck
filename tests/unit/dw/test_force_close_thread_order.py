"""Regression test for the ``/admin-force-close-thread`` ack ordering bug.

The original implementation called ``interaction.response.send_message``
AFTER ``thread.edit(archived=True)`` so when the slash command was
invoked from INSIDE the thread, archiving cut off the response
channel and Discord returned ``403 Forbidden (50083): Thread is
archived``. The thread WAS archived but the user saw "interacción
fallida".

The fix: acknowledge the interaction first (while the thread is
still open), then archive, then update the ephemeral to ✅ / ❌
via ``interaction.edit_original_response`` (best-effort — the
archive + audit-log row are what matter).

This test pins the call order using dependency-injected fakes that
record every call. We exercise the cog method directly via its
`.callback` attribute so the slash-command machinery is bypassed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class _CallTracker:
    """Records the order of operations for assertion."""

    def __init__(self) -> None:
        self.events: list[str] = []


class _FakeResponse:
    def __init__(self, tracker: _CallTracker) -> None:
        self._tracker = tracker

    async def send_message(self, *_args: Any, **_kwargs: Any) -> None:
        self._tracker.events.append("ack")


class _FakeBot:
    pool: object | None = None  # nullable like DwBot.pool

    def get_channel(self, _id: int) -> None:
        return None


class _FakeThread:
    def __init__(self, tracker: _CallTracker, *, raise_on_edit: bool = False) -> None:
        self._tracker = tracker
        self._raise = raise_on_edit
        self.id = 999
        self.mention = "<#999>"

    async def edit(self, **_kwargs: Any) -> None:
        self._tracker.events.append("archive")
        if self._raise:
            raise RuntimeError("synthetic archive failure")


class _FakeUser:
    id = 1
    mention = "<@1>"


class _FakeInteraction:
    def __init__(self, tracker: _CallTracker) -> None:
        self.response = _FakeResponse(tracker)
        self.user = _FakeUser()
        self._tracker = tracker

    async def edit_original_response(self, *, content: str) -> None:
        self._tracker.events.append(f"edit:{content[:20]}")


@pytest.mark.asyncio
async def test_force_close_thread_acks_before_archiving() -> None:
    """Regression: the ack must happen BEFORE thread.edit to avoid
    the 403 race when the slash is invoked from inside the thread."""
    from goldrush_deposit_withdraw.cogs.admin import AdminCog

    tracker = _CallTracker()
    bot = _FakeBot()
    cog = AdminCog(bot)  # type: ignore[arg-type]
    interaction = _FakeInteraction(tracker)
    thread = _FakeThread(tracker)

    await cog.force_close_thread.callback(  # type: ignore[attr-defined]
        cog, interaction, thread, "test reason"
    )

    # The very first event must be the ack — anything after archive is
    # the fragile path that 403'd in production.
    assert tracker.events[0] == "ack"
    assert "archive" in tracker.events
    assert tracker.events.index("ack") < tracker.events.index("archive")


@pytest.mark.asyncio
async def test_force_close_thread_handles_archive_failure_after_ack() -> None:
    """If ``thread.edit`` raises after the ack lands, the cog should
    edit the ephemeral with a user-facing error rather than crashing."""
    from goldrush_deposit_withdraw.cogs.admin import AdminCog

    tracker = _CallTracker()
    bot = _FakeBot()
    cog = AdminCog(bot)  # type: ignore[arg-type]
    interaction = _FakeInteraction(tracker)
    thread = _FakeThread(tracker, raise_on_edit=True)

    # Should not raise — failure is surfaced via edit_original_response.
    await cog.force_close_thread.callback(  # type: ignore[attr-defined]
        cog, interaction, thread, "test"
    )

    assert tracker.events[0] == "ack"
    # An edit happened after the failed archive carrying the error copy.
    assert any(e.startswith("edit:") for e in tracker.events)


def test_module_imports_cleanly() -> None:
    """Sanity check — keeps a non-async test for sync test discovery
    paranoia."""
    asyncio.run(asyncio.sleep(0))
