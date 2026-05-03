"""Unit tests for the ``#disputes`` channel poster (Story 9.2).

Story 9.2 says: each dispute open posts a new embed in ``#disputes``
and subsequent status changes EDIT THE SAME MESSAGE, with the
``discord_message_id`` persisted on the ``dw.disputes`` row.

The poster module is intentionally testable without a real Discord
client: callers pass a fake bot exposing ``get_channel`` and we
inject a fake channel that captures sends/edits. The fake pool
captures the SQL writes so we can assert the message_id round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import discord
import pytest
from deathroll_deposit_withdraw.disputes import (
    post_dispute_open_embed,
    update_dispute_status_embed,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edits: list[discord.Embed] = []
        self._raise_on_edit: type[Exception] | None = None

    async def edit(self, *, embed: discord.Embed) -> None:
        if self._raise_on_edit is not None:
            raise self._raise_on_edit("forced")
        self.edits.append(embed)


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[discord.Embed] = []
        self._next_message_id = 1000
        self.fetched: list[int] = []
        self._messages_by_id: dict[int, _FakeMessage] = {}
        self._fetch_raises: type[Exception] | None = None

    async def send(self, *, embed: discord.Embed) -> _FakeMessage:
        self._next_message_id += 1
        msg = _FakeMessage(self._next_message_id)
        self.sent.append(embed)
        self._messages_by_id[msg.id] = msg
        return msg

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        self.fetched.append(message_id)
        if self._fetch_raises is not None:
            raise self._fetch_raises("not found")
        if message_id not in self._messages_by_id:
            raise discord.NotFound(_FakeResponse(404), "missing")
        return self._messages_by_id[message_id]


class _FakeResponse:
    """Minimal aiohttp response stub for ``discord.NotFound``."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "Not Found"


class _FakeBot:
    def __init__(self, channel: _FakeChannel | None) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channel


class _FakePool:
    """Captures SQL traffic. We pre-load ``fetchrow_returns`` so the
    poster sees a deterministic sequence of return values."""

    def __init__(self, *, channel_id: int | None = 12345) -> None:
        self._channel_id = channel_id
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_returns: list[Any] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        # The channel-id lookup goes through the standard helper which
        # resolves through dw.global_config; we shortcut and return the
        # stored channel id when the query touches that column.
        if "global_config" in query and "channel_id_disputes" in args[0]:
            return {"value_int": self._channel_id} if self._channel_id else None
        # The poster reads the dispute row to find the existing message_id;
        # callers prime ``fetchrow_returns`` for these.
        if self.fetchrow_returns:
            return self.fetchrow_returns.pop(0)
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        return "OK"


SAMPLE_TS = datetime(2026, 4, 28, 1, 32, tzinfo=UTC)


# ---------------------------------------------------------------------------
# post_dispute_open_embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_dispute_open_embed_sends_to_channel_and_persists_message_id() -> None:
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    pool = _FakePool(channel_id=4242)

    await post_dispute_open_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        ticket_type="deposit",
        ticket_uid="deposit-12",
        opener_mention="<@222>",
        opener_role="admin",
        reason="cashier confirmed nothing arrived",
        opened_at=SAMPLE_TS,
    )

    # 1 send happened.
    assert len(channel.sent) == 1
    # The persisted UPDATE captured the auto-incremented message id (1001
    # — _FakeChannel starts at 1000 and increments on each send).
    persisted = [
        args for q, args in pool.executes if "discord_message_id" in q
    ]
    assert len(persisted) == 1
    persisted_args = persisted[0]
    assert persisted_args[0] == 1001  # the new message id
    assert persisted_args[1] == 17    # the dispute id


@pytest.mark.asyncio
async def test_post_dispute_open_embed_skips_when_channel_not_configured() -> None:
    """If ``channel_id_disputes`` isn't in dw.global_config yet (operator
    hasn't run ``/admin-setup``), the poster swallows quietly so the
    economic action that triggered it is not rolled back."""
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    pool = _FakePool(channel_id=None)

    await post_dispute_open_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        ticket_type="deposit",
        ticket_uid="deposit-12",
        opener_mention="<@222>",
        opener_role="admin",
        reason="reason",
        opened_at=SAMPLE_TS,
    )

    # No send, no UPDATE.
    assert channel.sent == []
    assert pool.executes == []


@pytest.mark.asyncio
async def test_post_dispute_open_embed_skips_when_channel_lookup_returns_none() -> None:
    """The channel id resolves but the bot can't find the actual channel
    (admin deleted it after setup) — same swallow-quietly behaviour."""
    bot = _FakeBot(None)
    pool = _FakePool(channel_id=4242)

    await post_dispute_open_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        ticket_type="deposit",
        ticket_uid="deposit-12",
        opener_mention="<@222>",
        opener_role="admin",
        reason="reason",
        opened_at=SAMPLE_TS,
    )
    # Nothing written.
    assert pool.executes == []


# ---------------------------------------------------------------------------
# update_dispute_status_embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_dispute_status_embed_edits_existing_message() -> None:
    """When ``discord_message_id`` is set on the row, the helper edits
    that message rather than posting a new one."""
    channel = _FakeChannel()
    # Pre-load a message in the channel that the helper will fetch+edit.
    channel._next_message_id = 9000
    sent_msg = await channel.send(embed=discord.Embed(title="seed"))
    bot = _FakeBot(channel)
    pool = _FakePool(channel_id=4242)
    # Prime the disputes-row lookup with the message_id.
    pool.fetchrow_returns = [
        {"discord_message_id": sent_msg.id, "ticket_uid": "deposit-12"}
    ]

    await update_dispute_status_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        new_embed=discord.Embed(title="resolved"),
    )

    # The edit went through — no new send.
    assert len(sent_msg.edits) == 1
    assert sent_msg.edits[0].title == "resolved"
    # And only the seed message exists in the channel.
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_update_dispute_status_embed_skips_when_no_message_id() -> None:
    """If the dispute row has no ``discord_message_id`` (post failed or the
    row predates Story 9.2's column) the helper skips silently — it does
    NOT post a fresh message because that would lose the original opener
    context."""
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    pool = _FakePool(channel_id=4242)
    pool.fetchrow_returns = [{"discord_message_id": None, "ticket_uid": "deposit-12"}]

    await update_dispute_status_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        new_embed=discord.Embed(title="resolved"),
    )

    assert channel.sent == []


@pytest.mark.asyncio
async def test_update_dispute_status_embed_swallows_message_disappeared() -> None:
    """The original Discord message was deleted by an admin manually —
    the helper logs and swallows, never propagating the 404 back to the
    caller (which would otherwise abort the resolve/reject flow)."""
    channel = _FakeChannel()
    bot = _FakeBot(channel)
    pool = _FakePool(channel_id=4242)
    pool.fetchrow_returns = [
        {"discord_message_id": 99999, "ticket_uid": "deposit-12"}
    ]

    # No message with id=99999 — fetch_message will raise NotFound.
    await update_dispute_status_embed(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        dispute_id=17,
        new_embed=discord.Embed(title="resolved"),
    )

    # No raise. Nothing edited (the original message is gone).
    assert channel.sent == []
    assert channel.fetched == [99999]
