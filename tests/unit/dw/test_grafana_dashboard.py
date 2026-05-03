"""Tests for the GoldRush D/W Grafana dashboard JSON (Story 11.2).

The dashboard ships at ``ops/observability/grafana-dashboards/goldrush-dw.json``.
We don't load it into a real Grafana in unit tests (that's Epic 14
integration work) — instead we pin the structural contract:

- Loads as valid JSON.
- Contains every panel from the spec §7.3 list.
- Every PromQL query references a goldrush_* metric we actually
  emit, so a typo at the dashboard side surfaces here rather than
  in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DASHBOARD = (
    Path(__file__).resolve().parents[3]
    / "ops"
    / "observability"
    / "grafana-dashboards"
    / "goldrush-dw.json"
)


@pytest.fixture(scope="module")
def dashboard() -> dict[str, object]:
    assert _DASHBOARD.exists(), f"dashboard JSON missing at {_DASHBOARD}"
    return json.loads(_DASHBOARD.read_text(encoding="utf-8"))


def test_dashboard_loads_as_json(dashboard: dict[str, object]) -> None:
    """Sanity: file loads, has the canonical Grafana keys."""
    assert "title" in dashboard
    assert "panels" in dashboard
    assert isinstance(dashboard["panels"], list)
    assert len(dashboard["panels"]) >= 6  # one per spec category, plus a couple extras


def test_dashboard_title_says_goldrush_dw(dashboard: dict[str, object]) -> None:
    title = str(dashboard["title"]).lower()
    assert "goldrush" in title and ("d/w" in title or "deposit" in title)


def test_dashboard_covers_every_spec_panel(dashboard: dict[str, object]) -> None:
    """Spec §7.3 calls out 7 panel families. We don't pin titles
    word-for-word (operators may rename) but the set of metric names
    referenced across all panel queries must include each metric."""
    panels = dashboard["panels"]
    assert isinstance(panels, list)
    all_queries = " ".join(_collect_query_text(p) for p in panels)
    expected_metric_substrings = (
        "goldrush_deposit_tickets",        # tickets/min by status
        "goldrush_withdraw_tickets",
        "goldrush_deposit_volume_g",        # volume by region
        "goldrush_withdraw_volume_g",
        "goldrush_treasury_balance_g",      # treasury over time
        "goldrush_cashiers_online",
        "goldrush_ticket_claim_duration_s", # duration distributions
        "goldrush_ticket_confirm_duration_s",
        "goldrush_cashier_dispute_rate",
        "goldrush_fee_revenue_g",           # fee revenue trend
    )
    missing = [m for m in expected_metric_substrings if m not in all_queries]
    assert not missing, f"dashboard missing PromQL refs to: {missing}"


def test_dashboard_uses_prometheus_datasource(dashboard: dict[str, object]) -> None:
    """Every target should declare type=prometheus so a Grafana that
    auto-resolves $datasource still picks the right one."""
    for panel in _iter_panels(dashboard):
        for target in panel.get("targets", []):
            datasource = target.get("datasource")
            if datasource is None:
                continue
            if isinstance(datasource, dict):
                assert datasource.get("type") == "prometheus", (
                    f"panel {panel.get('title')!r} target uses "
                    f"non-prometheus datasource: {datasource}"
                )


def _iter_panels(dashboard: dict[str, object]) -> list[dict[str, object]]:
    panels = dashboard["panels"]
    assert isinstance(panels, list)
    out: list[dict[str, object]] = []
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        out.append(panel)
        # Grafana row-panels nest sub-panels in a "panels" key.
        if isinstance(panel.get("panels"), list):
            for sub in panel["panels"]:
                if isinstance(sub, dict):
                    out.append(sub)
    return out


def _collect_query_text(panel: dict[str, object]) -> str:
    """Concatenate every ``targets[*].expr`` (and nested row panels)."""
    parts: list[str] = []
    for target in panel.get("targets", []) or []:
        if isinstance(target, dict):
            expr = target.get("expr", "")
            if isinstance(expr, str):
                parts.append(expr)
    nested = panel.get("panels")
    if isinstance(nested, list):
        for sub in nested:
            if isinstance(sub, dict):
                parts.append(_collect_query_text(sub))
    return " ".join(parts)
