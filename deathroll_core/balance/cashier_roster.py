"""Cashier roster snapshot for the online-cashiers live embed.

The query joins ``dw.cashier_status`` with ``dw.cashier_characters``
to surface, per cashier, the regions they cover. The snapshot is
read every 30 s by the live updater (Story 4.5) and on demand by
the deposit/withdraw matchmaker (Story 5/6).

Returned shape:

- ``online_by_region``  — Mapping of region → tuple of ``RosterEntry``.
                          A cashier with chars in BOTH regions appears
                          in BOTH buckets (a single entry per region
                          is what the live embed renders).
- ``on_break``          — Tuple of cashiers with status='break'.
                          Not bucketed by region because /cashier
                          set-status break is region-agnostic.
- ``offline_count``     — Plain integer; offline cashiers don't get
                          surfaced individually (no PII reason to).

The dataclasses are frozen so a snapshot passed through several
render layers cannot be mutated mid-pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from deathroll_core.db import Executor

_QUERY = """
SELECT
    cs.discord_id,
    cs.status,
    cs.last_active_at,
    COALESCE(
        ARRAY_AGG(DISTINCT cc.region) FILTER (WHERE cc.is_active),
        ARRAY[]::TEXT[]
    ) AS regions,
    COALESCE(
        ARRAY_AGG(DISTINCT cc.faction) FILTER (WHERE cc.is_active),
        ARRAY[]::TEXT[]
    ) AS factions
FROM dw.cashier_status cs
LEFT JOIN dw.cashier_characters cc
       ON cc.discord_id = cs.discord_id
GROUP BY cs.discord_id, cs.status, cs.last_active_at
"""


CashierStatus = Literal["online", "offline", "break"]


@dataclass(frozen=True)
class RosterEntry:
    """One cashier in the roster snapshot."""

    discord_id: int
    status: CashierStatus
    regions: tuple[str, ...]
    factions: tuple[str, ...]
    last_active_at: datetime


@dataclass(frozen=True)
class RosterSnapshot:
    """Aggregate snapshot rendered by the live embed every 30 s.

    ``online_by_region`` is a frozen-per-call dict; the values are
    tuples so consumers cannot inadvertently grow them. The keys
    are the regions present in the data (``EU`` / ``NA`` today;
    additional regions surface automatically once cashier_characters
    grows).
    """

    online_by_region: Mapping[str, tuple[RosterEntry, ...]]
    on_break: tuple[RosterEntry, ...]
    offline_count: int


async def fetch_online_roster(executor: Executor) -> RosterSnapshot:
    """Return the live roster snapshot.

    The query reads from ``dw.cashier_status`` and
    ``dw.cashier_characters``; the bot's ``deathroll_dw`` role has
    SELECT on both tables (per migration 0004 grants).
    """
    rows: list[Any] = await executor.fetch(_QUERY)
    online_by_region: dict[str, list[RosterEntry]] = {}
    on_break_list: list[RosterEntry] = []
    offline_count = 0

    for row in rows:
        regions = tuple(row["regions"]) if row["regions"] else ()
        factions = tuple(row["factions"]) if row["factions"] else ()
        entry = RosterEntry(
            discord_id=int(row["discord_id"]),
            status=row["status"],
            regions=regions,
            factions=factions,
            last_active_at=row["last_active_at"],
        )
        if entry.status == "online":
            for region in regions:
                online_by_region.setdefault(region, []).append(entry)
        elif entry.status == "break":
            on_break_list.append(entry)
        else:
            offline_count += 1

    # Freeze the per-region lists into tuples for immutability.
    frozen_by_region: dict[str, tuple[RosterEntry, ...]] = {
        region: tuple(entries) for region, entries in online_by_region.items()
    }
    return RosterSnapshot(
        online_by_region=frozen_by_region,
        on_break=tuple(on_break_list),
        offline_count=offline_count,
    )


__all__ = [
    "CashierStatus",
    "RosterEntry",
    "RosterSnapshot",
    "fetch_online_roster",
]
