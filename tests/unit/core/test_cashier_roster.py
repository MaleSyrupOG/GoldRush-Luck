"""Unit tests for `deathroll_core.balance.cashier_roster`.

Returns the live snapshot the online-cashiers embed (and the
matchmaking worker downstream) reads. Three buckets:

- ``online``   — status='online' rows, grouped by region.
- ``on_break`` — status='break' rows.
- ``offline_count`` — count of status='offline' rows (no PII —
  just the integer).

A cashier may have multiple ``dw.cashier_characters`` entries in
different regions; the regions tuple is computed from their active
chars.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from deathroll_core.balance.cashier_roster import (
    RosterEntry,
    RosterSnapshot,
    fetch_online_roster,
)

SAMPLE_TS = datetime(2026, 5, 2, 18, 0, tzinfo=UTC)


class _FakeExec:
    """Returns parametrised rows from ``fetch``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self._rows


@pytest.mark.asyncio
async def test_groups_online_cashiers_by_region() -> None:
    """Two online cashiers, one EU one NA → two region buckets."""
    rows = [
        {
            "discord_id": 1,
            "status": "online",
            "regions": ["EU"],
            "factions": ["Horde"],
            "last_active_at": SAMPLE_TS,
        },
        {
            "discord_id": 2,
            "status": "online",
            "regions": ["NA"],
            "factions": ["Alliance"],
            "last_active_at": SAMPLE_TS,
        },
    ]
    snapshot = await fetch_online_roster(_FakeExec(rows))
    assert sorted(snapshot.online_by_region.keys()) == ["EU", "NA"]
    assert len(snapshot.online_by_region["EU"]) == 1
    assert len(snapshot.online_by_region["NA"]) == 1
    assert snapshot.online_by_region["EU"][0].discord_id == 1
    assert snapshot.online_by_region["NA"][0].discord_id == 2


@pytest.mark.asyncio
async def test_cashier_with_multiple_regions_appears_in_each() -> None:
    """A cashier with EU + NA chars must appear in both buckets so the
    region-section reader sees them when filtering by region."""
    rows = [
        {
            "discord_id": 7,
            "status": "online",
            "regions": ["EU", "NA"],
            "factions": ["Horde"],
            "last_active_at": SAMPLE_TS,
        },
    ]
    snapshot = await fetch_online_roster(_FakeExec(rows))
    assert {7} == {e.discord_id for e in snapshot.online_by_region["EU"]}
    assert {7} == {e.discord_id for e in snapshot.online_by_region["NA"]}


@pytest.mark.asyncio
async def test_on_break_cashiers_listed_separately() -> None:
    rows = [
        {
            "discord_id": 3,
            "status": "break",
            "regions": ["EU"],
            "factions": ["Alliance"],
            "last_active_at": SAMPLE_TS,
        },
    ]
    snapshot = await fetch_online_roster(_FakeExec(rows))
    assert snapshot.online_by_region == {}
    assert len(snapshot.on_break) == 1
    assert snapshot.on_break[0].discord_id == 3


@pytest.mark.asyncio
async def test_offline_cashiers_counted_only() -> None:
    """Offline cashiers contribute to the count only — no PII surface."""
    rows = [
        {
            "discord_id": 4,
            "status": "offline",
            "regions": ["EU"],
            "factions": ["Horde"],
            "last_active_at": SAMPLE_TS,
        },
        {
            "discord_id": 5,
            "status": "offline",
            "regions": ["NA"],
            "factions": ["Horde"],
            "last_active_at": SAMPLE_TS,
        },
    ]
    snapshot = await fetch_online_roster(_FakeExec(rows))
    assert snapshot.offline_count == 2
    assert snapshot.online_by_region == {}
    assert snapshot.on_break == ()


@pytest.mark.asyncio
async def test_empty_roster_returns_empty_snapshot() -> None:
    snapshot = await fetch_online_roster(_FakeExec([]))
    assert snapshot.online_by_region == {}
    assert snapshot.on_break == ()
    assert snapshot.offline_count == 0


def test_snapshot_dataclasses_are_frozen() -> None:
    snap = RosterSnapshot(online_by_region={}, on_break=(), offline_count=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.offline_count = 5  # type: ignore[misc]
    entry = RosterEntry(
        discord_id=1,
        status="online",
        regions=("EU",),
        factions=("Horde",),
        last_active_at=SAMPLE_TS,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.discord_id = 999  # type: ignore[misc]
