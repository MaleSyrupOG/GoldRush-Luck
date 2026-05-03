"""Tests for ``goldrush_deposit_withdraw.metrics`` (Story 11.1).

The metrics module owns a CUSTOM ``prometheus_client.CollectorRegistry``
(not the default global) so the test suite scrapes a known set of
metric families without polluting other prometheus consumers.

Spec §7.3 lists 10 metric families:

  goldrush_deposit_tickets_total{status}
  goldrush_withdraw_tickets_total{status}
  goldrush_deposit_volume_g_total{region}
  goldrush_withdraw_volume_g_total{region}
  goldrush_treasury_balance_g
  goldrush_cashiers_online{region}
  goldrush_ticket_claim_duration_s{ticket_type}
  goldrush_ticket_confirm_duration_s{ticket_type}
  goldrush_cashier_dispute_rate{cashier_id}
  goldrush_fee_revenue_g_total

Type choices documented in metrics.py: counters with absolute totals
read from the DB are emitted as Gauges (because Prometheus Counter
semantics require monotonic increase, but the bot restarts would
reset a true Counter — Gauges of cumulative DB aggregates are more
honest about that). Histograms stay Histograms because they're
populated via observe() at event time.
"""

from __future__ import annotations

import prometheus_client
import pytest

from goldrush_deposit_withdraw.metrics import (
    REGISTRY,
    record_claim_duration,
    record_confirm_duration,
    refresh_from_db,
)


# ---------------------------------------------------------------------------
# Registry shape — every metric family from spec §7.3 must be present.
# ---------------------------------------------------------------------------


_EXPECTED_FAMILIES = {
    "goldrush_deposit_tickets",
    "goldrush_withdraw_tickets",
    "goldrush_deposit_volume_g",
    "goldrush_withdraw_volume_g",
    "goldrush_treasury_balance_g",
    "goldrush_cashiers_online",
    "goldrush_ticket_claim_duration_s",
    "goldrush_ticket_confirm_duration_s",
    "goldrush_cashier_dispute_rate",
    "goldrush_fee_revenue_g",
}


def test_registry_exposes_every_spec_metric() -> None:
    """Spec §7.3 family list is covered, not by exact name match (Prometheus
    appends ``_total`` to Counter family names) but by family base."""
    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    for family in _EXPECTED_FAMILIES:
        assert family in rendered, f"missing metric family: {family}"


def test_registry_has_no_default_globals() -> None:
    """A custom registry — process_/python_/gc_ defaults from the global
    registry should NOT be in this scrape."""
    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    # process/python defaults would appear in default REGISTRY but we
    # use a private one. (process_cpu_seconds is the canonical
    # default-collector marker.)
    assert "process_cpu_seconds" not in rendered


# ---------------------------------------------------------------------------
# Histograms — observed at event time
# ---------------------------------------------------------------------------


def test_record_claim_duration_increments_histogram() -> None:
    """Observing the claim->confirm gap bumps the histogram bucket."""
    before = _scrape_histogram_count(
        "goldrush_ticket_claim_duration_s", ticket_type="deposit"
    )
    record_claim_duration(ticket_type="deposit", seconds=12.5)
    after = _scrape_histogram_count(
        "goldrush_ticket_claim_duration_s", ticket_type="deposit"
    )
    assert after == before + 1


def test_record_confirm_duration_uses_ticket_type_label() -> None:
    record_confirm_duration(ticket_type="withdraw", seconds=0.87)
    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    # ticket_type="withdraw" appears in the label set with at least one obs.
    assert 'goldrush_ticket_confirm_duration_s_count{ticket_type="withdraw"}' in rendered


# ---------------------------------------------------------------------------
# refresh_from_db — pulls aggregates and sets gauges
# ---------------------------------------------------------------------------


class _FakePool:
    """Returns canned aggregates for each known SELECT the refresher emits."""

    def __init__(self, *, payloads: dict[str, object]) -> None:
        self._payloads = payloads
        self.queries: list[str] = []

    async def fetch(self, query: str, *_args: object) -> object:
        self.queries.append(query)
        # Find the matching payload by substring match — keeps tests resilient
        # to whitespace tweaks in the SQL.
        for needle, payload in self._payloads.items():
            if needle in query:
                return payload
        return []

    async def fetchval(self, query: str, *_args: object) -> object:
        self.queries.append(query)
        for needle, payload in self._payloads.items():
            if needle in query:
                return payload
        return None


@pytest.mark.asyncio
async def test_refresh_from_db_sets_treasury_balance() -> None:
    pool = _FakePool(payloads={
        "FROM core.balances WHERE discord_id = 0": 1_234_567,
    })
    await refresh_from_db(pool=pool)  # type: ignore[arg-type]

    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    # Prometheus uses scientific notation for large floats — assert the
    # value is present without pinning the exact format.
    treasury_lines = [
        line for line in rendered.splitlines()
        if line.startswith("goldrush_treasury_balance_g ")
    ]
    assert treasury_lines, "treasury gauge missing from scrape"
    # Parse the trailing number — accepts "1.234567e+06" or "1234567.0".
    value = float(treasury_lines[0].split()[-1])
    assert value == 1_234_567.0


@pytest.mark.asyncio
async def test_refresh_from_db_sets_ticket_status_counts() -> None:
    pool = _FakePool(payloads={
        "FROM dw.deposit_tickets": [
            {"status": "open", "n": 3},
            {"status": "confirmed", "n": 17},
        ],
        "FROM dw.withdraw_tickets": [
            {"status": "open", "n": 1},
            {"status": "cancelled", "n": 2},
        ],
        "FROM core.balances WHERE discord_id = 0": 0,
    })
    await refresh_from_db(pool=pool)  # type: ignore[arg-type]

    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    assert 'goldrush_deposit_tickets{status="open"} 3.0' in rendered
    assert 'goldrush_deposit_tickets{status="confirmed"} 17.0' in rendered
    assert 'goldrush_withdraw_tickets{status="cancelled"} 2.0' in rendered


@pytest.mark.asyncio
async def test_refresh_from_db_handles_empty_db_gracefully() -> None:
    """Brand-new DB: no tickets, no cashiers, no fees. The refresher
    should NOT raise; metrics simply stay at their default zero state."""
    pool = _FakePool(payloads={})
    # Should not raise.
    await refresh_from_db(pool=pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scrape_histogram_count(family: str, **labels: str) -> float:
    """Read the ``<family>_count`` value for the given label set."""
    rendered = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{family}_count{{{label_str}}}"
    for line in rendered.splitlines():
        if line.startswith(needle):
            return float(line.split()[-1])
    return 0.0
