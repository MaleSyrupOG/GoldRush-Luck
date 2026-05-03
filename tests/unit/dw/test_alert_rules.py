"""Tests for the DeathRoll D/W Prometheus alert rules (Story 11.3).

The rules ship at ``ops/observability/alerts/deathroll-dw.yml`` and
get loaded by Prometheus via its ``rule_files`` glob. We don't load
them into a real Prometheus in unit tests — instead we pin:

- The YAML loads cleanly.
- All 5 alert names from spec §7.3 are present.
- Every alert references a metric we actually emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_RULES = (
    Path(__file__).resolve().parents[3]
    / "ops"
    / "observability"
    / "alerts"
    / "deathroll-dw.yml"
)

_EXPECTED_ALERTS = {
    "DeathRollDWStuckTicket",
    "DeathRollNoCashiersOnline",
    "DeathRollTreasuryDrop",
    "DeathRollHighCancellationRate",
    "DeathRollUnusualCashierActivity",
}


@pytest.fixture(scope="module")
def rules() -> dict[str, object]:
    assert _RULES.exists(), f"alert rules YAML missing at {_RULES}"
    return yaml.safe_load(_RULES.read_text(encoding="utf-8"))


def test_rules_yaml_has_groups(rules: dict[str, object]) -> None:
    assert "groups" in rules
    assert isinstance(rules["groups"], list)
    assert len(rules["groups"]) >= 1


def test_rules_cover_every_spec_alert(rules: dict[str, object]) -> None:
    """Spec §7.3 lists 5 named alerts. Each must appear at least once."""
    groups = rules["groups"]
    assert isinstance(groups, list)
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []) or []:
            if isinstance(rule, dict) and "alert" in rule:
                seen.add(str(rule["alert"]))
    missing = _EXPECTED_ALERTS - seen
    assert not missing, f"alert rules missing: {missing}"


def test_every_alert_has_severity_and_summary(rules: dict[str, object]) -> None:
    """Operator-friendly metadata is mandatory — Alertmanager
    Discord routing reads ``severity`` and the summary annotation."""
    groups = rules["groups"]
    assert isinstance(groups, list)
    for group in groups:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []) or []:
            if not (isinstance(rule, dict) and "alert" in rule):
                continue
            labels = rule.get("labels") or {}
            annotations = rule.get("annotations") or {}
            alert_name = rule.get("alert")
            assert (
                isinstance(labels, dict) and "severity" in labels
            ), f"{alert_name}: missing labels.severity"
            assert (
                isinstance(annotations, dict) and "summary" in annotations
            ), f"{alert_name}: missing annotations.summary"


def test_alert_expressions_reference_known_metrics(rules: dict[str, object]) -> None:
    """Every alert's expr must mention at least one ``deathroll_*``
    metric. Catches typos like ``deathroll_treasury_g`` (without ``_balance``)
    that would otherwise silently never fire."""
    known_prefixes = (
        "deathroll_deposit_tickets",
        "deathroll_withdraw_tickets",
        "deathroll_deposit_volume_g",
        "deathroll_withdraw_volume_g",
        "deathroll_treasury_balance_g",
        "deathroll_cashiers_online",
        "deathroll_ticket_claim_duration_s",
        "deathroll_ticket_confirm_duration_s",
        "deathroll_cashier_dispute_rate",
        "deathroll_fee_revenue_g",
    )
    groups = rules["groups"]
    assert isinstance(groups, list)
    for group in groups:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []) or []:
            if not (isinstance(rule, dict) and "alert" in rule):
                continue
            expr = str(rule.get("expr", ""))
            assert any(p in expr for p in known_prefixes), (
                f"alert {rule['alert']!r} expr does not reference any known "
                f"deathroll_* metric: {expr!r}"
            )
