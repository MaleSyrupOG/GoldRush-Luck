"""Unit tests for `goldrush_deposit_withdraw.cashiers.live_updater` (Story 4.5).

The updater edits the live ``#online-cashiers`` embed every 30 s.
Tests target three layers:

- ``tick``: one iteration. Idempotent post / edit / repost branches
  mirror the welcome reconciler.
- ``OnlineCashiersUpdater``: wraps tick in a cancellable asyncio loop.
  We exercise start / stop / cancel without sleeping for 30 seconds.
- The roster query, the embed builder, and the message routing
  combine into the user-visible result tested in
  ``test_dw_embeds.py`` and ``test_cashier_roster.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import discord
import pytest
from goldrush_core.balance.cashier_roster import RosterEntry, RosterSnapshot
from goldrush_deposit_withdraw.cashiers.live_updater import (
    OnlineCashiersUpdater,
    tick,
)

SAMPLE_TS = datetime(2026, 5, 2, 18, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes (small variants of the welcome-reconciler fakes)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edits: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self, channel_id: int = 100) -> None:
        self.id = channel_id
        self.messages: dict[int, _FakeMessage] = {}
        self._next_id = 999

    async def send(self, *, embed: discord.Embed) -> _FakeMessage:
        msg = _FakeMessage(self._next_id)
        self.messages[self._next_id] = msg
        self._next_id += 1
        return msg

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        if message_id in self.messages:
            return self.messages[message_id]
        raise discord.NotFound(
            response=_FakeResponse(),  # type: ignore[arg-type]
            message=f"unknown {message_id}",
        )


class _FakeResponse:
    status = 404
    reason = "Not Found"


class _FakeBot:
    def __init__(self, channels: dict[int, _FakeChannel]) -> None:
        self._channels = channels

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channels.get(channel_id)


class _FakePool:
    """Returns a parametrised ``RosterSnapshot`` and tracks message_id state."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        existing_message_id: int | None = None,
        existing_channel_id: int | None = None,
    ) -> None:
        self._rows = rows
        self._message_id = existing_message_id
        self._channel_id = existing_channel_id
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[dict[str, Any]]:
        return self._rows

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> dict[str, Any] | None:
        if "FROM dw.dynamic_embeds" in query:
            if self._message_id is None and self._channel_id is None:
                return None
            return {
                "embed_key": "online_cashiers",
                "channel_id": self._channel_id,
                "message_id": self._message_id,
                "title": "Online cashiers",
                "description": "live",
                "color_hex": "#5DBE5A",
                "fields": [],
                "image_url": None,
                "footer_text": None,
            }
        raise AssertionError(f"unexpected fetchrow {query!r}")

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        self.executes.append((query, args))
        if "INSERT INTO dw.dynamic_embeds" in query:
            self._channel_id = args[1]
        elif "UPDATE dw.dynamic_embeds" in query and "message_id" in query:
            self._message_id = args[0]
        return "OK"

    @property
    def message_id(self) -> int | None:
        return self._message_id


# ---------------------------------------------------------------------------
# tick — single iteration
# ---------------------------------------------------------------------------


def test_tick_first_run_inserts_row_and_posts_message() -> None:
    pool = _FakePool(rows=[], existing_message_id=None, existing_channel_id=None)
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    async def _exercise() -> int | None:
        return await tick(pool=pool, bot=bot, channel_id=100)  # type: ignore[arg-type]

    msg_id = asyncio.run(_exercise())
    assert msg_id is not None
    # The pool persisted both the channel id and the message id.
    assert pool.message_id == msg_id
    # Channel actually received a single send.
    assert len(channel.messages) == 1


def test_tick_existing_message_edits_in_place() -> None:
    channel = _FakeChannel(channel_id=100)
    existing = _FakeMessage(42)
    channel.messages[42] = existing
    bot = _FakeBot(channels={100: channel})
    pool = _FakePool(rows=[], existing_message_id=42, existing_channel_id=100)

    async def _exercise() -> int | None:
        return await tick(pool=pool, bot=bot, channel_id=100)  # type: ignore[arg-type]

    msg_id = asyncio.run(_exercise())
    assert msg_id == 42
    assert len(existing.edits) == 1
    # No new message sent.
    assert len(channel.messages) == 1


def test_tick_message_deleted_triggers_repost() -> None:
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})
    pool = _FakePool(rows=[], existing_message_id=42, existing_channel_id=100)

    async def _exercise() -> int | None:
        return await tick(pool=pool, bot=bot, channel_id=100)  # type: ignore[arg-type]

    msg_id = asyncio.run(_exercise())
    assert msg_id is not None
    assert msg_id != 42
    assert pool.message_id == msg_id


def test_tick_skips_when_channel_unknown() -> None:
    pool = _FakePool(rows=[])
    bot = _FakeBot(channels={})  # channel 100 not present

    async def _exercise() -> int | None:
        return await tick(pool=pool, bot=bot, channel_id=100)  # type: ignore[arg-type]

    msg_id = asyncio.run(_exercise())
    assert msg_id is None


# ---------------------------------------------------------------------------
# OnlineCashiersUpdater — start / stop semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updater_runs_initial_tick_then_can_be_stopped() -> None:
    pool = _FakePool(rows=[])
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    updater = OnlineCashiersUpdater(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        channel_id=100,
        interval=10.0,  # short enough that we won't hit it during the test
    )

    updater.start()
    # Yield once so the first tick gets a chance to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await updater.stop()

    # The very first tick should have produced one message (or attempted it).
    assert len(channel.messages) >= 1


@pytest.mark.asyncio
async def test_updater_start_is_idempotent() -> None:
    pool = _FakePool(rows=[])
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    updater = OnlineCashiersUpdater(
        pool=pool,  # type: ignore[arg-type]
        bot=bot,  # type: ignore[arg-type]
        channel_id=100,
        interval=10.0,
    )
    updater.start()
    updater.start()  # second call must be a no-op, not crash.
    await asyncio.sleep(0)
    await updater.stop()


# ---------------------------------------------------------------------------
# Sanity: the snapshot dataclass shape matches the embed builder expectations
# ---------------------------------------------------------------------------


def test_snapshot_round_trip_into_embed() -> None:
    """Construct a RosterSnapshot, render via the embed builder, assert
    the EU and NA sections both list their respective cashiers."""
    from goldrush_core.embeds.dw_tickets import online_cashiers_live_embed

    snap = RosterSnapshot(
        online_by_region={
            "EU": (
                RosterEntry(
                    discord_id=1,
                    status="online",
                    regions=("EU",),
                    factions=("Horde",),
                    last_active_at=SAMPLE_TS,
                ),
            ),
            "NA": (
                RosterEntry(
                    discord_id=2,
                    status="online",
                    regions=("NA",),
                    factions=("Alliance",),
                    last_active_at=SAMPLE_TS,
                ),
            ),
        },
        on_break=(),
        offline_count=3,
    )
    embed = online_cashiers_live_embed(snapshot=snap, last_updated=SAMPLE_TS)
    # Field names carry region labels; values carry mentions; footer
    # carries the offline count. Read all three surfaces so the test
    # is robust to layout changes.
    field_blob = " ".join(
        f"{f.name} {f.value or ''}" for f in embed.fields
    )
    footer_text = (embed.footer.text or "") if embed.footer else ""
    blob = (embed.description or "") + " " + field_blob + " " + footer_text
    assert "EU" in blob
    assert "NA" in blob
    assert "<@1>" in blob
    assert "<@2>" in blob
    assert "3" in blob  # offline count in footer
