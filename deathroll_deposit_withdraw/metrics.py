"""Prometheus metrics for the Deposit/Withdraw bot (Story 11.1).

Spec §7.3 lists 10 metric families. The ones populated from
periodic DB aggregates are exposed as Gauges (despite the spec
naming them ``_total``) because:

- The bot restarts on deploy, which would reset a true Counter.
  Reading the absolute totals from the DB on each refresh is more
  honest about the long-run cumulative — a Gauge of "current
  cumulative count" represents reality.
- Prometheus alert / Grafana dashboard math (``rate``, ``increase``,
  ``deriv``) works on Gauges too. The few cases where Counter
  semantics matter (rate of growth) are handled by ``deriv()`` on
  the Gauge.

Histograms (``ticket_claim_duration_s``, ``ticket_confirm_duration_s``)
keep Histogram type because they're populated via ``observe()`` at
event time, not derived from DB state.

A custom ``CollectorRegistry`` is used so the test suite can scrape
a known set without picking up Python process / GC defaults from
the global registry. The HTTP exposition server (started from
``DwBot.on_ready``) binds the same custom registry on port 9101.
"""

from __future__ import annotations

import time
from typing import Any

import prometheus_client
import structlog
from deathroll_core.db import Executor
from prometheus_client import CollectorRegistry, Gauge, Histogram

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom registry — every metric registers here, NOT on the global registry.
# ---------------------------------------------------------------------------


REGISTRY = CollectorRegistry()


# ---------------------------------------------------------------------------
# Metric families (spec §7.3)
# ---------------------------------------------------------------------------


_DEPOSIT_TICKETS = Gauge(
    "deathroll_deposit_tickets",
    "Total deposit tickets by status (DB aggregate, refreshed every 30 s).",
    labelnames=("status",),
    registry=REGISTRY,
)

_WITHDRAW_TICKETS = Gauge(
    "deathroll_withdraw_tickets",
    "Total withdraw tickets by status (DB aggregate, refreshed every 30 s).",
    labelnames=("status",),
    registry=REGISTRY,
)

_DEPOSIT_VOLUME = Gauge(
    "deathroll_deposit_volume_g",
    "Cumulative confirmed deposit gold volume by region.",
    labelnames=("region",),
    registry=REGISTRY,
)

_WITHDRAW_VOLUME = Gauge(
    "deathroll_withdraw_volume_g",
    "Cumulative confirmed withdraw gold volume by region.",
    labelnames=("region",),
    registry=REGISTRY,
)

_TREASURY_BALANCE = Gauge(
    "deathroll_treasury_balance_g",
    "Bot-tracked treasury balance (core.balances WHERE discord_id=0).",
    registry=REGISTRY,
)

_CASHIERS_ONLINE = Gauge(
    "deathroll_cashiers_online",
    "Distinct cashiers in status='online' by region of their characters.",
    labelnames=("region",),
    registry=REGISTRY,
)

# Histograms — populated via observe() in the ticket cog.
# Buckets cover the operational range: a few seconds to a few hours.
_CLAIM_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200)
_CONFIRM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

_CLAIM_DURATION = Histogram(
    "deathroll_ticket_claim_duration_s",
    "Time from cashier claim to confirm in seconds.",
    labelnames=("ticket_type",),
    buckets=_CLAIM_BUCKETS,
    registry=REGISTRY,
)

_CONFIRM_DURATION = Histogram(
    "deathroll_ticket_confirm_duration_s",
    "Latency of the dw.confirm_* SECURITY DEFINER call (cog timing).",
    labelnames=("ticket_type",),
    buckets=_CONFIRM_BUCKETS,
    registry=REGISTRY,
)

_DISPUTE_RATE = Gauge(
    "deathroll_cashier_dispute_rate",
    "Disputes per cashier as a fraction of their confirmed tickets.",
    labelnames=("cashier_id",),
    registry=REGISTRY,
)

_FEE_REVENUE = Gauge(
    "deathroll_fee_revenue_g",
    "Cumulative fee revenue (sum of withdraw fees on confirmed tickets).",
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Event-time recording helpers (used by the ticket cog).
# ---------------------------------------------------------------------------


def record_claim_duration(*, ticket_type: str, seconds: float) -> None:
    """Observe the gap between claim and confirm. Caller passes the
    pre-computed delta in seconds (typically ``confirmed_at -
    claimed_at`` from the SDF return)."""
    _CLAIM_DURATION.labels(ticket_type=ticket_type).observe(seconds)


def record_confirm_duration(*, ticket_type: str, seconds: float) -> None:
    """Observe the cog-side latency of the SECURITY DEFINER confirm
    call. Caller measures with ``time.perf_counter()`` around the
    wrapper invocation."""
    _CONFIRM_DURATION.labels(ticket_type=ticket_type).observe(seconds)


# ---------------------------------------------------------------------------
# refresh_from_db — pulls aggregates and updates Gauges.
# ---------------------------------------------------------------------------


async def refresh_from_db(*, pool: Executor) -> None:
    """Re-read DB aggregates and set every Gauge.

    Called every 30 s from :class:`MetricsRefresherWorker`. Best-effort
    per metric — a single broken query (e.g. permissions blip) logs
    and continues so the rest of the metrics stay fresh.
    """
    started = time.perf_counter()

    # Treasury balance.
    try:
        balance = await pool.fetchval(
            "SELECT balance FROM core.balances WHERE discord_id = 0"
        )
        if balance is not None:
            _TREASURY_BALANCE.set(int(balance))
    except Exception as e:
        _log.warning("metrics_refresh_treasury_failed", error=str(e))

    # Ticket counts by status — both families.
    await _refresh_status_gauge(
        pool=pool,
        gauge=_DEPOSIT_TICKETS,
        sql="SELECT status, COUNT(*) AS n FROM dw.deposit_tickets GROUP BY status",
        family="deposit_tickets",
    )
    await _refresh_status_gauge(
        pool=pool,
        gauge=_WITHDRAW_TICKETS,
        sql="SELECT status, COUNT(*) AS n FROM dw.withdraw_tickets GROUP BY status",
        family="withdraw_tickets",
    )

    # Volumes by region (only confirmed tickets count toward "processed").
    await _refresh_region_gauge(
        pool=pool,
        gauge=_DEPOSIT_VOLUME,
        sql=(
            "SELECT region, COALESCE(SUM(amount), 0) AS v "
            "FROM dw.deposit_tickets WHERE status = 'confirmed' "
            "GROUP BY region"
        ),
        family="deposit_volume",
    )
    await _refresh_region_gauge(
        pool=pool,
        gauge=_WITHDRAW_VOLUME,
        sql=(
            "SELECT region, COALESCE(SUM(amount), 0) AS v "
            "FROM dw.withdraw_tickets WHERE status = 'confirmed' "
            "GROUP BY region"
        ),
        family="withdraw_volume",
    )

    # Cashiers online by region. A cashier with chars in multiple
    # regions counts in each region — same accounting as the live
    # roster embed.
    try:
        rows = await pool.fetch(
            """
            SELECT cc.region, COUNT(DISTINCT cs.discord_id) AS n
              FROM dw.cashier_status  cs
              JOIN dw.cashier_characters cc ON cc.discord_id = cs.discord_id
             WHERE cs.status = 'online'
               AND cc.removed_at IS NULL
             GROUP BY cc.region
            """
        )
        # Reset before populating so a region that goes to zero clears.
        _CASHIERS_ONLINE.clear()
        for row in rows:
            _CASHIERS_ONLINE.labels(region=str(row["region"])).set(int(row["n"]))
    except Exception as e:
        _log.warning("metrics_refresh_cashiers_online_failed", error=str(e))

    # Fee revenue — cumulative across confirmed withdraws.
    try:
        fees = await pool.fetchval(
            """
            SELECT COALESCE(SUM(fee), 0)
              FROM dw.withdraw_tickets
             WHERE status = 'confirmed'
               AND fee IS NOT NULL
            """
        )
        if fees is not None:
            _FEE_REVENUE.set(int(fees))
    except Exception as e:
        _log.warning("metrics_refresh_fee_revenue_failed", error=str(e))

    # Dispute rate per cashier — disputes / confirmations. Only
    # surfaces cashiers with at least one confirmation; otherwise
    # the rate is undefined and we skip the row to avoid noise in
    # Grafana.
    try:
        rows = await pool.fetch(
            """
            WITH confirmations AS (
                SELECT claimed_by AS cashier_id, COUNT(*) AS confs
                  FROM (
                      SELECT claimed_by FROM dw.deposit_tickets
                       WHERE status = 'confirmed' AND claimed_by IS NOT NULL
                      UNION ALL
                      SELECT claimed_by FROM dw.withdraw_tickets
                       WHERE status = 'confirmed' AND claimed_by IS NOT NULL
                  ) c
                 GROUP BY claimed_by
            ),
            disputes AS (
                SELECT t.claimed_by AS cashier_id, COUNT(*) AS n
                  FROM dw.disputes d
                  LEFT JOIN dw.deposit_tickets  t
                            ON t.ticket_uid = d.ticket_uid AND d.ticket_type = 'deposit'
                 WHERE t.claimed_by IS NOT NULL
                 GROUP BY t.claimed_by
                UNION ALL
                SELECT t.claimed_by AS cashier_id, COUNT(*) AS n
                  FROM dw.disputes d
                  LEFT JOIN dw.withdraw_tickets t
                            ON t.ticket_uid = d.ticket_uid AND d.ticket_type = 'withdraw'
                 WHERE t.claimed_by IS NOT NULL
                 GROUP BY t.claimed_by
            )
            SELECT c.cashier_id, c.confs,
                   COALESCE((SELECT SUM(d.n) FROM disputes d
                              WHERE d.cashier_id = c.cashier_id), 0) AS dispute_count
              FROM confirmations c
            """
        )
        _DISPUTE_RATE.clear()
        for row in rows:
            confs = int(row["confs"])
            if confs == 0:
                continue
            disputes = int(row["dispute_count"])
            rate = disputes / confs
            _DISPUTE_RATE.labels(cashier_id=str(row["cashier_id"])).set(rate)
    except Exception as e:
        _log.warning("metrics_refresh_dispute_rate_failed", error=str(e))

    elapsed = time.perf_counter() - started
    _log.debug("metrics_refreshed", duration_s=round(elapsed, 3))


async def _refresh_status_gauge(
    *,
    pool: Executor,
    gauge: Gauge,
    sql: str,
    family: str,
) -> None:
    try:
        rows = await pool.fetch(sql)
        gauge.clear()
        for row in rows:
            gauge.labels(status=str(row["status"])).set(int(row["n"]))
    except Exception as e:
        _log.warning("metrics_refresh_status_failed", family=family, error=str(e))


async def _refresh_region_gauge(
    *,
    pool: Executor,
    gauge: Gauge,
    sql: str,
    family: str,
) -> None:
    try:
        rows = await pool.fetch(sql)
        gauge.clear()
        for row in rows:
            gauge.labels(region=str(row["region"])).set(int(row["v"]))
    except Exception as e:
        _log.warning("metrics_refresh_region_failed", family=family, error=str(e))


# ---------------------------------------------------------------------------
# HTTP exposition — wrapped so tests can mock without binding a port.
# ---------------------------------------------------------------------------


def start_metrics_server(*, port: int = 9101) -> Any:
    """Start the Prometheus exposition HTTP server on ``port``.

    The server runs in a daemon thread (the ``prometheus_client``
    helper handles thread management). Returns the HTTPServer instance
    so callers can shut it down deterministically on bot stop, mirroring
    the pattern of the live-updater + worker classes.
    """
    server, thread = prometheus_client.exposition.start_http_server(
        port=port, registry=REGISTRY
    )
    _log.info("metrics_http_server_started", port=port)
    return server


__all__ = [
    "REGISTRY",
    "record_claim_duration",
    "record_confirm_duration",
    "refresh_from_db",
    "start_metrics_server",
]
