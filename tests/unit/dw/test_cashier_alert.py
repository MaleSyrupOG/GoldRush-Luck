"""Unit tests for `goldrush_deposit_withdraw.cashiers.alert` (Story 5.3).

The alert poster runs after a ticket is opened. It fetches the live
roster, filters compatible cashiers, builds the alert embed with
the compatible-list field, and posts it in ``#cashier-alerts``
prefaced by the ``@cashier`` role mention so the channel pings
the right people.

Tests use in-process fakes for pool + bot + channel — no Discord
client required.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from goldrush_deposit_withdraw.cashiers.alert import post_cashier_alert

SAMPLE_TS = datetime(2026, 5, 2, 19, 0, tzinfo=UTC)


class _FakeMessage:
    def __init__(self) -> None:
        self.id = 9999


class _FakeChannel:
    def __init__(self, channel_id: int = 100) -> None:
        self.id = channel_id
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        content: str | None = None,
        *,
        embed: Any | None = None,
        allowed_mentions: Any | None = None,
    ) -> _FakeMessage:
        self.sent.append(
            {"content": content, "embed": embed, "allowed_mentions": allowed_mentions}
        )
        return _FakeMessage()


class _FakeBot:
    def __init__(self, channels: dict[int, _FakeChannel]) -> None:
        self._channels = channels

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channels.get(channel_id)


class _FakePool:
    """Returns a fixed roster + a configured channel id."""

    def __init__(
        self,
        *,
        alert_channel_id: int | None,
        roster_rows: list[dict[str, Any]],
    ) -> None:
        self._alert_channel_id = alert_channel_id
        self._roster_rows = roster_rows

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> dict[str, Any] | None:
        if "FROM dw.global_config" in query:
            if args[0] == "channel_id_cashier_alerts" and self._alert_channel_id is not None:
                return {"value_int": self._alert_channel_id}
        return None

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[dict[str, Any]]:
        return self._roster_rows


def _entry(*, discord_id: int, status: str, regions: list[str], factions: list[str]) -> dict[str, Any]:
    return {
        "discord_id": discord_id,
        "status": status,
        "regions": regions,
        "factions": factions,
        "last_active_at": SAMPLE_TS,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_post_alert_includes_compatible_cashier_for_eu_horde_ticket() -> None:
    """Spec §5.3: with one EU Horde cashier online and an EU Horde
    ticket, the embed lists that cashier as compatible."""
    pool = _FakePool(
        alert_channel_id=100,
        roster_rows=[
            _entry(discord_id=42, status="online", regions=["EU"], factions=["Horde"]),
        ],
    )
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    asyncio.run(
        post_cashier_alert(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            ticket_uid="deposit-1",
            ticket_type="deposit",
            region="EU",
            faction="Horde",
            amount=50000,
            ticket_channel_mention="<#1234>",
        )
    )

    assert len(channel.sent) == 1
    embed = channel.sent[0]["embed"]
    field_blob = " ".join(
        f"{f.name} {f.value or ''}" for f in embed.fields
    )
    assert "<@42>" in field_blob
    # The cashier role mention is the message content so it pings
    # the role in addition to the embed body.
    content = channel.sent[0]["content"] or ""
    assert "@" in content  # @cashier or role syntax


# ---------------------------------------------------------------------------
# No compatible cashier
# ---------------------------------------------------------------------------


def test_post_alert_renders_none_online_placeholder() -> None:
    """No matching cashier → the field still renders (with a
    "_none online for this region/faction_" placeholder) so the
    cashier-alerts channel is informative either way."""
    pool = _FakePool(
        alert_channel_id=100,
        roster_rows=[
            # Wrong region.
            _entry(discord_id=1, status="online", regions=["NA"], factions=["Horde"]),
            # Wrong faction.
            _entry(discord_id=2, status="online", regions=["EU"], factions=["Alliance"]),
            # On break (excluded from online_by_region).
            _entry(discord_id=3, status="break", regions=["EU"], factions=["Horde"]),
        ],
    )
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    asyncio.run(
        post_cashier_alert(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            ticket_uid="deposit-2",
            ticket_type="deposit",
            region="EU",
            faction="Horde",
            amount=50000,
            ticket_channel_mention="<#1234>",
        )
    )

    embed = channel.sent[0]["embed"]
    field_blob = " ".join(f.value or "" for f in embed.fields)
    assert "none online" in field_blob.lower()


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


def test_post_alert_skips_when_channel_id_unconfigured() -> None:
    """No ``channel_id_cashier_alerts`` in dw.global_config (operator
    hasn't run /admin setup yet) → skip silently. The ticket channel
    still has the in-thread @cashier mention as the fallback."""
    pool = _FakePool(alert_channel_id=None, roster_rows=[])
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    asyncio.run(
        post_cashier_alert(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            ticket_uid="deposit-3",
            ticket_type="deposit",
            region="EU",
            faction="Horde",
            amount=50000,
            ticket_channel_mention="<#1234>",
        )
    )
    # No send call because the channel id is unknown.
    assert channel.sent == []


def test_post_alert_skips_when_channel_not_in_cache() -> None:
    """Configured id resolves to None on bot.get_channel → skip."""
    pool = _FakePool(alert_channel_id=999, roster_rows=[])
    bot = _FakeBot(channels={})  # channel 999 absent

    # Does not raise.
    asyncio.run(
        post_cashier_alert(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            ticket_uid="deposit-4",
            ticket_type="deposit",
            region="EU",
            faction="Horde",
            amount=50000,
            ticket_channel_mention="<#1234>",
        )
    )


@pytest.mark.parametrize("ticket_type", ["deposit", "withdraw"])
def test_post_alert_works_for_both_ticket_types(ticket_type: str) -> None:
    """Withdraw alerts use the same poster — single code path."""
    pool = _FakePool(
        alert_channel_id=100,
        roster_rows=[
            _entry(discord_id=42, status="online", regions=["EU"], factions=["Horde"]),
        ],
    )
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    asyncio.run(
        post_cashier_alert(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            ticket_uid=f"{ticket_type}-1",
            ticket_type=ticket_type,  # type: ignore[arg-type]
            region="EU",
            faction="Horde",
            amount=50000,
            ticket_channel_mention="<#1234>",
        )
    )
    assert len(channel.sent) == 1
