"""Unit tests for `goldrush_core.balance.cashier_matcher`.

Given a (region, faction) pair, returns the list of cashiers
currently online whose active chars match. Used by:

- Cashier-alert embed (Story 5.3): "compatible cashiers: <list>".
- Future matchmaking worker that auto-claims tickets when a
  matching cashier comes online.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from goldrush_core.balance.cashier_matcher import find_compatible_cashiers
from goldrush_core.balance.cashier_roster import RosterEntry, RosterSnapshot

SAMPLE_TS = datetime(2026, 5, 2, 18, 0, tzinfo=UTC)


def _entry(*, discord_id: int, regions: tuple[str, ...], factions: tuple[str, ...]) -> RosterEntry:
    return RosterEntry(
        discord_id=discord_id,
        status="online",
        regions=regions,
        factions=factions,
        last_active_at=SAMPLE_TS,
    )


def _snapshot(entries: list[RosterEntry]) -> RosterSnapshot:
    by_region: dict[str, list[RosterEntry]] = {}
    for e in entries:
        for r in e.regions:
            by_region.setdefault(r, []).append(e)
    return RosterSnapshot(
        online_by_region={r: tuple(es) for r, es in by_region.items()},
        on_break=(),
        offline_count=0,
    )


def test_returns_only_matching_region_and_faction() -> None:
    eu_horde = _entry(discord_id=1, regions=("EU",), factions=("Horde",))
    eu_alliance = _entry(discord_id=2, regions=("EU",), factions=("Alliance",))
    na_horde = _entry(discord_id=3, regions=("NA",), factions=("Horde",))
    snap = _snapshot([eu_horde, eu_alliance, na_horde])

    result = find_compatible_cashiers(snap, region="EU", faction="Horde")
    assert [e.discord_id for e in result] == [1]


def test_returns_empty_when_no_match() -> None:
    snap = _snapshot([_entry(discord_id=1, regions=("EU",), factions=("Horde",))])
    result = find_compatible_cashiers(snap, region="NA", faction="Alliance")
    assert result == ()


def test_cashier_with_multiple_factions_matches_each() -> None:
    """A cashier with chars in both Horde and Alliance qualifies for either."""
    multi = _entry(
        discord_id=7, regions=("EU",), factions=("Horde", "Alliance")
    )
    snap = _snapshot([multi])

    horde_match = find_compatible_cashiers(snap, region="EU", faction="Horde")
    alliance_match = find_compatible_cashiers(snap, region="EU", faction="Alliance")
    assert [e.discord_id for e in horde_match] == [7]
    assert [e.discord_id for e in alliance_match] == [7]


def test_offline_cashier_not_matched() -> None:
    """Only entries that appeared in ``online_by_region`` qualify; an
    on-break or offline cashier is excluded by construction (the
    snapshot's online_by_region only contains status='online'
    entries)."""
    # Build a snapshot where EU Horde cashier is on break (not in online_by_region).
    snap = RosterSnapshot(
        online_by_region={},
        on_break=(_entry(discord_id=1, regions=("EU",), factions=("Horde",)),),
        offline_count=0,
    )
    assert find_compatible_cashiers(snap, region="EU", faction="Horde") == ()


def test_invalid_region_rejected() -> None:
    snap = _snapshot([])
    with pytest.raises(ValueError):
        find_compatible_cashiers(snap, region="ASIA", faction="Horde")  # type: ignore[arg-type]
