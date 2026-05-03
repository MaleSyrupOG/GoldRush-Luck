"""Compatible-cashier filter for matchmaking and the cashier alert embed.

Given a roster snapshot and a (region, faction) pair, returns the
cashiers whose active chars match. The current design lives in
memory because the snapshot is small and refreshed every 30 s; if
the roster ever grows beyond a few hundred cashiers we'll push
the filter into a SQL query.

Used by:

- ``cashier_alert_embed`` (Story 5.3): the "compatible cashiers:
  <list>" line.
- The auto-claim worker (Story 8) when a ticket sits open and a
  matching cashier transitions to ``online``.

A bug that broadened the filter (e.g., dropped the faction match)
would silently expose tickets to the wrong cashiers — the tests
guard the exact set semantics.
"""

from __future__ import annotations

from typing import Literal

from deathroll_core.balance.cashier_roster import RosterEntry, RosterSnapshot

_VALID_REGIONS: frozenset[str] = frozenset({"EU", "NA"})
_VALID_FACTIONS: frozenset[str] = frozenset({"Alliance", "Horde"})

Region = Literal["EU", "NA"]
Faction = Literal["Alliance", "Horde"]


def find_compatible_cashiers(
    snapshot: RosterSnapshot, *, region: Region, faction: Faction
) -> tuple[RosterEntry, ...]:
    """Return cashiers online with a char in ``region`` AND faction match.

    The snapshot's ``online_by_region`` only contains
    ``status='online'`` entries by construction, so on-break and
    offline cashiers are naturally excluded.

    Order is the iteration order of the region bucket — we don't
    sort for fairness here; the matchmaker (Story 8) is responsible
    for FIFO scheduling.
    """
    if region not in _VALID_REGIONS:
        raise ValueError(f"invalid region {region!r}; expected one of {sorted(_VALID_REGIONS)}")
    if faction not in _VALID_FACTIONS:
        raise ValueError(
            f"invalid faction {faction!r}; expected one of {sorted(_VALID_FACTIONS)}"
        )

    bucket = snapshot.online_by_region.get(region, ())
    return tuple(e for e in bucket if faction in e.factions)


__all__ = ["Faction", "Region", "find_compatible_cashiers"]
