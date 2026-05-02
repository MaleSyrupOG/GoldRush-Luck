"""Snapshot tests for `goldrush_core.embeds.dw_tickets`.

Embeds are presentation surface — they need to be visually consistent
across every state and across redeploys. The tests below act as a
visual contract: they assert title, colour, key fields, and footer
text for every builder in the spec §5.6 list, plus the auxiliary
"awaiting cashier" / "wait instructions" embeds documented in
``reference_deposit_ticket_ux.md`` (the captured screenshot from the
incumbent bot Aleix wants us to replicate).

Tests are pure — they construct an embed from realistic kwargs and
read back the fields. No Discord client is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import discord
import pytest
from goldrush_core.embeds.dw_tickets import (
    COLOR_BUST,
    COLOR_EMBER,
    COLOR_GOLD,
    COLOR_HOUSE,
    COLOR_WIN,
    awaiting_cashier_embed,
    cashier_alert_embed,
    cashier_stats_embed,
    deposit_ticket_cancelled_embed,
    deposit_ticket_claimed_embed,
    deposit_ticket_confirmed_embed,
    deposit_ticket_open_embed,
    dispute_list_embed,
    dispute_open_embed,
    dispute_resolved_embed,
    how_to_deposit_dynamic_embed,
    online_cashiers_live_embed,
    treasury_balance_embed,
    wait_instructions_embed,
    withdraw_ticket_cancelled_embed,
    withdraw_ticket_claimed_embed,
    withdraw_ticket_confirmed_embed,
    withdraw_ticket_open_embed,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _field_dict(embed: discord.Embed) -> dict[str, str]:
    """Return ``{field.name: field.value}`` for easy assertion.

    Fields are appended in deterministic order in the builders, but the
    tests only care about presence + value, not ordering, except where
    explicitly noted.
    """
    return {f.name: (f.value or "") for f in embed.fields}


def _field_names(embed: discord.Embed) -> list[str]:
    """Return field names in declaration order."""
    return [f.name for f in embed.fields]


SAMPLE_TS = datetime(2026, 4, 27, 23, 32, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Palette sanity
# ---------------------------------------------------------------------------


def test_palette_constants_match_design_system() -> None:
    """Hexes match the GoldRush design system (Luck §6.3 + visual contract)."""
    assert COLOR_HOUSE == 0x5B7CC9
    assert COLOR_WIN == 0x5DBE5A
    assert COLOR_BUST == 0xD8231A
    assert COLOR_EMBER == 0xC8511C
    assert COLOR_GOLD == 0xF2B22A


# ---------------------------------------------------------------------------
# deposit_ticket_open_embed — "Submitted" state (HOUSE blue)
# ---------------------------------------------------------------------------


def _open_kwargs() -> dict[str, object]:
    return dict(
        ticket_uid="GRD-FRU6",
        char_name="Malesyrup",
        region="EU",
        faction="Horde",
        amount=50000,
        created_at=SAMPLE_TS,
    )


def test_deposit_open_embed_has_house_blue_color() -> None:
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    assert embed.color is not None
    assert embed.color.value == COLOR_HOUSE


def test_deposit_open_embed_title_marks_submitted() -> None:
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    assert embed.title is not None
    assert "Submitted" in embed.title
    assert "Deposit" in embed.title


def test_deposit_open_embed_displays_uid_in_fields() -> None:
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    fields = _field_dict(embed)
    # The visual contract uses "Deposit ID" — accept either ID or Deposit ID.
    id_value = fields.get("Deposit ID") or fields.get("ID") or ""
    assert "GRD-FRU6" in id_value


def test_deposit_open_embed_displays_character_region_faction() -> None:
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    fields = _field_dict(embed)
    assert fields.get("Character") == "Malesyrup"
    assert fields.get("Region") == "EU"
    assert fields.get("Faction") == "Horde"


def test_deposit_open_embed_amount_uses_thousands_separator() -> None:
    """The visual contract shows ``50,000g`` with comma separators."""
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    fields = _field_dict(embed)
    amount_value = fields.get("Amount") or ""
    assert "50,000" in amount_value
    assert amount_value.endswith("g")


def test_deposit_open_embed_carries_timestamp() -> None:
    embed = deposit_ticket_open_embed(**_open_kwargs())  # type: ignore[arg-type]
    assert embed.timestamp == SAMPLE_TS


def test_deposit_open_embed_amount_zero_safe() -> None:
    """Edge case: zero amount should not raise during rendering.

    The DB layer already rejects amount<=0, but the embed builder must
    not blow up if a future code path accidentally passes 0 — it should
    render cleanly so the bug is visible, not crash mid-flow.
    """
    kwargs = _open_kwargs() | {"amount": 0}
    embed = deposit_ticket_open_embed(**kwargs)  # type: ignore[arg-type]
    fields = _field_dict(embed)
    assert "0" in (fields.get("Amount") or "")


# ---------------------------------------------------------------------------
# awaiting_cashier_embed — "No EU Horde cashiers online" (BUST red)
# ---------------------------------------------------------------------------


def test_awaiting_cashier_embed_red_color_and_includes_region_faction() -> None:
    embed = awaiting_cashier_embed(region="EU", faction="Horde", ticket_type="deposit")
    assert embed.color is not None
    assert embed.color.value == COLOR_BUST
    blob = (embed.title or "") + (embed.description or "")
    assert "EU" in blob
    assert "Horde" in blob
    # Mentions that the ticket is open / waiting.
    assert "ticket" in blob.lower() or "cashier" in blob.lower()


def test_awaiting_cashier_embed_asks_user_to_stay() -> None:
    embed = awaiting_cashier_embed(region="NA", faction="Alliance", ticket_type="withdraw")
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    # Anti-disconnect prompt; we accept either "stay" or "remain in this channel".
    assert "stay" in blob.lower() or "remain" in blob.lower()


def test_awaiting_cashier_embed_handles_both_ticket_types() -> None:
    deposit = awaiting_cashier_embed(region="EU", faction="Horde", ticket_type="deposit")
    withdraw = awaiting_cashier_embed(region="EU", faction="Horde", ticket_type="withdraw")
    assert deposit.color is not None
    assert withdraw.color is not None
    # Both red, both same flow — distinct enough for visual recognition is OK
    # but the test just guards that the function accepts both literals.


# ---------------------------------------------------------------------------
# wait_instructions_embed — "Please wait..." (EMBER orange)
# ---------------------------------------------------------------------------


def test_wait_instructions_embed_ember_color() -> None:
    embed = wait_instructions_embed(ticket_type="deposit")
    assert embed.color is not None
    assert embed.color.value == COLOR_EMBER


def test_wait_instructions_embed_warns_against_premature_trade() -> None:
    """The whole point of this embed: do NOT trade gold yet."""
    embed = wait_instructions_embed(ticket_type="deposit")
    blob = ((embed.title or "") + (embed.description or "")).lower()
    assert "trade" in blob
    assert "wait" in blob or "cashier" in blob


# ---------------------------------------------------------------------------
# deposit_ticket_claimed_embed — claimed by cashier (GOLD)
# ---------------------------------------------------------------------------


def _claimed_kwargs() -> dict[str, object]:
    return dict(
        ticket_uid="GRD-FRU6",
        amount=50000,
        user_char_name="Malesyrup",
        cashier_mention="<@123456789>",
        cashier_char="GoldrushUSH",
        cashier_realm="Stormrage",
        cashier_region="NA",  # display as US per visual contract
        location="Orgrimmar · Valley of Strength · AH",
    )


def test_deposit_claimed_embed_gold_color() -> None:
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    assert embed.color is not None
    assert embed.color.value == COLOR_GOLD


def test_deposit_claimed_embed_title_says_claimed() -> None:
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    assert "Claimed" in (embed.title or "")


def test_deposit_claimed_embed_mentions_cashier_and_realm() -> None:
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    desc = embed.description or ""
    assert "<@123456789>" in desc
    assert "Stormrage" in desc


def test_deposit_claimed_embed_displays_us_for_na_region() -> None:
    """NA region is displayed as ``(US)`` per the visual contract."""
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    desc = embed.description or ""
    assert "(US)" in desc


def test_deposit_claimed_embed_displays_eu_region_as_eu() -> None:
    kwargs = _claimed_kwargs() | {"cashier_region": "EU"}
    embed = deposit_ticket_claimed_embed(**kwargs)  # type: ignore[arg-type]
    desc = embed.description or ""
    assert "(EU)" in desc


def test_deposit_claimed_embed_carries_anti_phishing_warning() -> None:
    """CRITICAL anti-phishing UX: cashier never initiates the trade.

    Removing this warning would weaken the whole anti-fraud story —
    the test guards it explicitly.
    """
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    # The warning lives somewhere in the embed — could be description tail
    # or a dedicated field. Match the substantive phrase.
    assert "NEVER" in blob or "never" in blob
    assert "trade" in blob.lower()


def test_deposit_claimed_embed_shows_id_amount_character_location() -> None:
    embed = deposit_ticket_claimed_embed(**_claimed_kwargs())  # type: ignore[arg-type]
    fields = _field_dict(embed)
    id_value = fields.get("ID") or fields.get("Deposit ID") or ""
    assert "GRD-FRU6" in id_value
    assert "50,000" in (fields.get("Amount") or "")
    # Cashier's ingame char is shown; user char optional.
    assert "GoldrushUSH" in (fields.get("Character") or fields.get("Cashier Character") or "")
    assert (fields.get("Location") or "") == "Orgrimmar · Valley of Strength · AH"


# ---------------------------------------------------------------------------
# deposit_ticket_confirmed_embed — final state (WIN green)
# ---------------------------------------------------------------------------


def test_deposit_confirmed_embed_green_color_and_celebrates() -> None:
    embed = deposit_ticket_confirmed_embed(
        ticket_uid="GRD-FRU6",
        amount=50000,
        new_balance=125000,
        confirmed_at=SAMPLE_TS,
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_WIN
    assert "Confirmed" in (embed.title or "")


def test_deposit_confirmed_embed_shows_amount_and_new_balance() -> None:
    embed = deposit_ticket_confirmed_embed(
        ticket_uid="GRD-FRU6",
        amount=50000,
        new_balance=125000,
        confirmed_at=SAMPLE_TS,
    )
    fields = _field_dict(embed)
    blob = " ".join(fields.values()) + (embed.description or "")
    assert "50,000" in blob
    assert "125,000" in blob


def test_deposit_confirmed_embed_carries_timestamp() -> None:
    embed = deposit_ticket_confirmed_embed(
        ticket_uid="GRD-FRU6",
        amount=50000,
        new_balance=125000,
        confirmed_at=SAMPLE_TS,
    )
    assert embed.timestamp == SAMPLE_TS


# ---------------------------------------------------------------------------
# deposit_ticket_cancelled_embed — terminal cancel (BUST red)
# ---------------------------------------------------------------------------


def test_deposit_cancelled_embed_red_and_includes_reason() -> None:
    embed = deposit_ticket_cancelled_embed(
        ticket_uid="GRD-FRU6",
        reason="user requested",
        cancelled_at=SAMPLE_TS,
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_BUST
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "user requested" in blob


# ---------------------------------------------------------------------------
# withdraw_ticket_open_embed — must show fee + amount_delivered
# ---------------------------------------------------------------------------


def _withdraw_open_kwargs() -> dict[str, object]:
    return dict(
        ticket_uid="GRD-WX1A",
        char_name="Malesyrup",
        region="EU",
        faction="Horde",
        amount=50000,
        fee=1000,
        amount_delivered=49000,
        created_at=SAMPLE_TS,
    )


def test_withdraw_open_embed_house_blue() -> None:
    embed = withdraw_ticket_open_embed(**_withdraw_open_kwargs())  # type: ignore[arg-type]
    assert embed.color is not None
    assert embed.color.value == COLOR_HOUSE


def test_withdraw_open_embed_title_says_withdraw_submitted() -> None:
    embed = withdraw_ticket_open_embed(**_withdraw_open_kwargs())  # type: ignore[arg-type]
    title = embed.title or ""
    assert "Withdraw" in title
    assert "Submitted" in title


def test_withdraw_open_embed_shows_amount_fee_and_delivered_breakdown() -> None:
    """The withdraw user sees three values: gross, fee, net delivered.

    50,000 G request - 1,000 fee = 49,000 delivered ingame.
    """
    embed = withdraw_ticket_open_embed(**_withdraw_open_kwargs())  # type: ignore[arg-type]
    fields = _field_dict(embed)
    blob = " ".join(fields.values()) + (embed.description or "")
    assert "50,000" in blob
    assert "1,000" in blob
    assert "49,000" in blob


# ---------------------------------------------------------------------------
# withdraw_ticket_claimed_embed
# ---------------------------------------------------------------------------


def test_withdraw_claimed_embed_gold_with_anti_phishing() -> None:
    embed = withdraw_ticket_claimed_embed(
        ticket_uid="GRD-WX1A",
        amount=50000,
        amount_delivered=49000,
        user_char_name="Malesyrup",
        cashier_mention="<@123456789>",
        cashier_char="GoldrushUSH",
        cashier_realm="Stormrage",
        cashier_region="EU",
        location="Stormwind · Trade District · Bank",
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_GOLD
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    # For withdraw, anti-phishing reminder is in the OPPOSITE direction:
    # the user must wait for the cashier's whisper / trade. Either phrasing
    # is acceptable so long as the warning is present.
    assert "trade" in blob.lower()
    assert "Stormwind" in blob or "Stormwind · Trade District · Bank" in blob


# ---------------------------------------------------------------------------
# withdraw_ticket_confirmed_embed
# ---------------------------------------------------------------------------


def test_withdraw_confirmed_embed_green_with_amount_delivered() -> None:
    embed = withdraw_ticket_confirmed_embed(
        ticket_uid="GRD-WX1A",
        amount=50000,
        fee=1000,
        amount_delivered=49000,
        new_balance=75000,
        confirmed_at=SAMPLE_TS,
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_WIN
    blob = " ".join(_field_dict(embed).values()) + (embed.description or "")
    assert "49,000" in blob


# ---------------------------------------------------------------------------
# withdraw_ticket_cancelled_embed — must show REFUNDED indicator
# ---------------------------------------------------------------------------


def test_withdraw_cancelled_embed_red_and_announces_refund() -> None:
    """Cancelled withdraw releases the locked balance back to the user."""
    embed = withdraw_ticket_cancelled_embed(
        ticket_uid="GRD-WX1A",
        refunded_amount=50000,
        reason="cashier unavailable",
        cancelled_at=SAMPLE_TS,
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_BUST
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "REFUND" in blob.upper()
    assert "50,000" in blob


# ---------------------------------------------------------------------------
# cashier_alert_embed — pings #cashier-alerts
# ---------------------------------------------------------------------------


def test_cashier_alert_embed_includes_ticket_meta_and_link() -> None:
    embed = cashier_alert_embed(
        ticket_uid="GRD-FRU6",
        ticket_type="deposit",
        region="EU",
        faction="Horde",
        amount=50000,
        channel_mention="<#987654321>",
    )
    blob = (embed.title or "") + (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "GRD-FRU6" in blob
    assert "EU" in blob
    assert "Horde" in blob
    assert "50,000" in blob
    assert "<#987654321>" in blob
    # ticket type is announced one way or another
    assert "deposit" in blob.lower()


def test_cashier_alert_embed_handles_withdraw() -> None:
    embed = cashier_alert_embed(
        ticket_uid="GRD-WX1A",
        ticket_type="withdraw",
        region="NA",
        faction="Alliance",
        amount=10000,
        channel_mention="<#111>",
    )
    blob = (embed.title or "") + (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "withdraw" in blob.lower()


# ---------------------------------------------------------------------------
# online_cashiers_live_embed — list of online cashiers (refreshed every 30s)
# ---------------------------------------------------------------------------


def _empty_snapshot() -> Any:
    """Build a RosterSnapshot with no cashiers — used by the empty-state tests."""
    from goldrush_core.balance.cashier_roster import RosterSnapshot

    return RosterSnapshot(online_by_region={}, on_break=(), offline_count=0)


def _two_cashier_snapshot() -> Any:
    """Build a RosterSnapshot with one EU online + one NA on break."""
    from goldrush_core.balance.cashier_roster import RosterEntry, RosterSnapshot

    eu = RosterEntry(
        discord_id=1,
        status="online",
        regions=("EU",),
        factions=("Horde",),
        last_active_at=SAMPLE_TS,
    )
    na = RosterEntry(
        discord_id=2,
        status="break",
        regions=("NA",),
        factions=("Alliance",),
        last_active_at=SAMPLE_TS,
    )
    return RosterSnapshot(
        online_by_region={"EU": (eu,)},
        on_break=(na,),
        offline_count=0,
    )


def test_online_cashiers_live_embed_with_no_cashiers() -> None:
    """Empty roster — embed must still render cleanly."""
    embed = online_cashiers_live_embed(snapshot=_empty_snapshot(), last_updated=SAMPLE_TS)
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "no" in blob.lower() or "0" in blob or "online" in blob.lower()


def test_online_cashiers_live_embed_lists_each_cashier() -> None:
    embed = online_cashiers_live_embed(
        snapshot=_two_cashier_snapshot(), last_updated=SAMPLE_TS
    )
    # Field names carry region labels ("EU" / "NA" / "On break"); values
    # carry the cashier mentions. Concatenate both so the assertion reads
    # the whole embed surface.
    blob = (embed.description or "") + " ".join(
        f"{f.name} {f.value or ''}" for f in embed.fields
    )
    assert "<@1>" in blob
    assert "<@2>" in blob
    assert "EU" in blob
    assert "On break" in blob  # NA cashier rendered under "On break" subsection


def test_online_cashiers_live_embed_carries_timestamp() -> None:
    embed = online_cashiers_live_embed(snapshot=_empty_snapshot(), last_updated=SAMPLE_TS)
    assert embed.timestamp == SAMPLE_TS


def test_online_cashiers_live_embed_offline_count_in_footer() -> None:
    """The offline count surfaces in the footer per spec §5.6."""
    from goldrush_core.balance.cashier_roster import RosterSnapshot

    snap = RosterSnapshot(online_by_region={}, on_break=(), offline_count=4)
    embed = online_cashiers_live_embed(snapshot=snap, last_updated=SAMPLE_TS)
    footer_text = (embed.footer.text or "") if embed.footer else ""
    body = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "4" in footer_text or "4" in body


# ---------------------------------------------------------------------------
# cashier_stats_embed — admin ephemeral
# ---------------------------------------------------------------------------


def test_cashier_stats_embed_renders_all_known_metrics() -> None:
    embed = cashier_stats_embed(
        cashier_mention="<@555>",
        deposits_completed=42,
        deposits_cancelled=3,
        withdraws_completed=18,
        withdraws_cancelled=1,
        total_volume_g=12500000,
        total_online_seconds=86400,
        avg_claim_to_confirm_s=215,
        last_active_at=SAMPLE_TS,
    )
    blob = (embed.title or "") + (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "<@555>" in blob
    assert "42" in blob
    assert "12,500,000" in blob


def test_cashier_stats_embed_handles_null_average_and_last_active() -> None:
    """A brand-new cashier may have no claim yet — must not crash."""
    embed = cashier_stats_embed(
        cashier_mention="<@555>",
        deposits_completed=0,
        deposits_cancelled=0,
        withdraws_completed=0,
        withdraws_cancelled=0,
        total_volume_g=0,
        total_online_seconds=0,
        avg_claim_to_confirm_s=None,
        last_active_at=None,
    )
    # No exception is the assertion. Embed should be a valid object.
    assert isinstance(embed, discord.Embed)


# ---------------------------------------------------------------------------
# dispute_open_embed
# ---------------------------------------------------------------------------


def test_dispute_open_embed_red_with_full_meta() -> None:
    embed = dispute_open_embed(
        dispute_id=17,
        ticket_uid="GRD-FRU6",
        ticket_type="deposit",
        opener_mention="<@222>",
        opener_role="user",
        reason="cashier confirmed but I never sent gold",
        opened_at=SAMPLE_TS,
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_BUST
    blob = (embed.title or "") + (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "17" in blob
    assert "GRD-FRU6" in blob
    assert "deposit" in blob.lower()
    assert "<@222>" in blob
    assert "cashier confirmed" in blob


# ---------------------------------------------------------------------------
# dispute_resolved_embed
# ---------------------------------------------------------------------------


def test_dispute_resolved_embed_green_when_resolved() -> None:
    embed = dispute_resolved_embed(
        dispute_id=17,
        ticket_uid="GRD-FRU6",
        resolution="full refund issued from treasury",
        resolved_by_mention="<@1>",
        resolved_at=SAMPLE_TS,
        status="resolved",
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_WIN
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "full refund" in blob


def test_dispute_resolved_embed_red_when_rejected() -> None:
    embed = dispute_resolved_embed(
        dispute_id=18,
        ticket_uid="GRD-FRU6",
        resolution="evidence does not support claim",
        resolved_by_mention="<@1>",
        resolved_at=SAMPLE_TS,
        status="rejected",
    )
    assert embed.color is not None
    assert embed.color.value == COLOR_BUST


# ---------------------------------------------------------------------------
# dispute_list_embed (Story 9.1)
# ---------------------------------------------------------------------------


def test_dispute_list_embed_empty_state() -> None:
    """When there are no disputes, the embed renders a friendly empty
    description rather than blowing up on an empty list."""
    embed = dispute_list_embed(disputes=[], status_filter="open")
    blob = (embed.title or "") + (embed.description or "")
    assert "open" in blob.lower()
    # Empty state should explicitly say so — operators rely on this when
    # confirming there's nothing to triage.
    assert "no" in blob.lower() or "empty" in blob.lower() or "0" in blob


def test_dispute_list_embed_renders_each_row() -> None:
    """Each row in the list shows id / ticket / status / opened_at."""
    rows = [
        {
            "id": 17,
            "ticket_type": "deposit",
            "ticket_uid": "deposit-12",
            "status": "open",
            "opener_id": 222,
            "opened_at": SAMPLE_TS,
        },
        {
            "id": 18,
            "ticket_type": "withdraw",
            "ticket_uid": "withdraw-3",
            "status": "investigating",
            "opener_id": 333,
            "opened_at": SAMPLE_TS,
        },
    ]
    embed = dispute_list_embed(disputes=rows, status_filter=None)
    blob = (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "17" in blob
    assert "18" in blob
    assert "deposit-12" in blob
    assert "withdraw-3" in blob


def test_dispute_list_embed_titles_with_status_filter() -> None:
    embed = dispute_list_embed(disputes=[], status_filter="resolved")
    blob = (embed.title or "") + (embed.description or "")
    assert "resolved" in blob.lower()


# ---------------------------------------------------------------------------
# how_to_deposit_dynamic_embed — renders a row from dw.dynamic_embeds
# ---------------------------------------------------------------------------


def test_how_to_deposit_dynamic_embed_renders_minimum() -> None:
    """The minimum payload — title + description — must render."""
    embed = how_to_deposit_dynamic_embed(
        title="How to deposit",
        description="Open `/deposit` and follow the cashier's instructions.",
    )
    assert embed.title == "How to deposit"
    assert embed.description == "Open `/deposit` and follow the cashier's instructions."


def test_how_to_deposit_dynamic_embed_applies_color_and_image_and_footer() -> None:
    embed = how_to_deposit_dynamic_embed(
        title="How to deposit",
        description="Step 1: ...",
        color_hex="#F2B22A",
        image_url="https://example.com/banner.png",
        footer_text="Updated 2026-04-29",
    )
    assert embed.color is not None
    assert embed.color.value == 0xF2B22A
    assert embed.image.url == "https://example.com/banner.png"
    assert embed.footer.text == "Updated 2026-04-29"


def test_how_to_deposit_dynamic_embed_parses_fields_json() -> None:
    """A JSON array of {name,value,inline} objects becomes embed fields."""
    embed = how_to_deposit_dynamic_embed(
        title="t",
        description="d",
        fields_json='[{"name":"Min","value":"200g","inline":true},{"name":"Max","value":"200,000g","inline":true}]',
    )
    fields = _field_dict(embed)
    assert fields["Min"] == "200g"
    assert fields["Max"] == "200,000g"


def test_how_to_deposit_dynamic_embed_invalid_fields_json_falls_back() -> None:
    """Malformed JSON should not crash the bot — fall back to no fields.

    The validation already happens in the EditDynamicEmbedInput model;
    this guard is a final safety net so a corrupt DB row does not panic
    the renderer.
    """
    embed = how_to_deposit_dynamic_embed(
        title="t",
        description="d",
        fields_json="not-json",
    )
    assert _field_names(embed) == []


# ---------------------------------------------------------------------------
# treasury_balance_embed — admin ephemeral
# ---------------------------------------------------------------------------


def test_treasury_balance_embed_shows_balance() -> None:
    embed = treasury_balance_embed(
        balance=4_550_000,
        last_sweep_at=SAMPLE_TS,
        last_sweep_amount=1_000_000,
    )
    blob = (embed.title or "") + (embed.description or "") + " ".join(f.value or "" for f in embed.fields)
    assert "4,550,000" in blob
    assert "1,000,000" in blob


def test_treasury_balance_embed_handles_no_sweep_history() -> None:
    embed = treasury_balance_embed(
        balance=0,
        last_sweep_at=None,
        last_sweep_amount=None,
    )
    blob = " ".join(f.value or "" for f in embed.fields)
    # No exception + zero balance rendered
    assert "0" in blob or "0" in (embed.description or "")


# ---------------------------------------------------------------------------
# Property-style: every builder returns a discord.Embed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "embed",
    [
        deposit_ticket_open_embed(  # type: ignore[arg-type]
            ticket_uid="GRD-AAAA",
            char_name="Foo",
            region="EU",
            faction="Horde",
            amount=200,
            created_at=SAMPLE_TS,
        ),
        awaiting_cashier_embed(region="EU", faction="Horde", ticket_type="deposit"),
        wait_instructions_embed(ticket_type="deposit"),
        deposit_ticket_claimed_embed(
            ticket_uid="GRD-AAAA",
            amount=200,
            user_char_name="Foo",
            cashier_mention="<@1>",
            cashier_char="Bar",
            cashier_realm="Stormrage",
            cashier_region="EU",
            location="Loc",
        ),
        deposit_ticket_confirmed_embed(
            ticket_uid="GRD-AAAA",
            amount=200,
            new_balance=200,
            confirmed_at=SAMPLE_TS,
        ),
        deposit_ticket_cancelled_embed(
            ticket_uid="GRD-AAAA", reason="r", cancelled_at=SAMPLE_TS
        ),
    ],
)
def test_builders_return_discord_embed(embed: discord.Embed) -> None:
    assert isinstance(embed, discord.Embed)
    # Discord's hard limit on total embed length is 6000 chars; we stay
    # well under that for any of our deterministic builders.
    total = (
        len(embed.title or "")
        + len(embed.description or "")
        + sum(len(f.name) + len(f.value or "") for f in embed.fields)
        + len((embed.footer.text or "") if embed.footer else "")
    )
    assert total < 6000
