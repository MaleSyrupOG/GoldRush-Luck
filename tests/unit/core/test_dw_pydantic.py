"""Unit tests for `goldrush_core.models.dw_pydantic`.

Modal-input models receive raw strings from Discord; the tests cover
both the happy path (valid strings normalise to typed objects) and the
hostile path (every invalid format produces a ValidationError with a
helpful message).

Domain models are exercised by constructing them from realistic
``dict`` payloads — what asyncpg returns when calling ``dict(record)``
on a fetched row.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from goldrush_core.models.dw_pydantic import (
    BalanceSnapshot,
    CashierCharacter,
    CashierStats,
    CashierStatus,
    DepositModalInput,
    DepositTicket,
    Dispute,
    EditDynamicEmbedInput,
    UserSnapshot,
    WithdrawModalInput,
    WithdrawTicket,
)


# ---------------------------------------------------------------------------
# DepositModalInput — happy paths
# ---------------------------------------------------------------------------


def test_deposit_modal_happy_path() -> None:
    m = DepositModalInput(
        char_name="Malesyrup",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=50000,
    )
    assert m.char_name == "Malesyrup"
    assert m.realm == "Stormrage"
    assert m.region == "EU"
    assert m.faction == "Horde"
    assert m.amount == 50000


def test_deposit_modal_normalises_region_case() -> None:
    """The user might type 'eu', 'EU', 'Eu' — all should resolve to 'EU'."""
    for raw in ("eu", "EU", "Eu", " eU "):
        m = DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region=raw, faction="Horde", amount=50000,
        )
        assert m.region == "EU"


def test_deposit_modal_normalises_faction_case() -> None:
    """Same for faction — title-cased canonical form."""
    for raw, expected in (
        ("alliance", "Alliance"),
        ("ALLIANCE", "Alliance"),
        ("Alliance", "Alliance"),
        ("horde", "Horde"),
        ("HORDE", "Horde"),
    ):
        m = DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region="EU", faction=raw, amount=50000,
        )
        assert m.faction == expected


def test_deposit_modal_amount_string_is_parsed() -> None:
    """Discord modals return strings; the validator parses them."""
    m = DepositModalInput(
        char_name="Malesyrup", realm="Stormrage",
        region="EU", faction="Horde",
        amount="25000",  # type: ignore[arg-type]  # the model accepts str at runtime
    )
    assert m.amount == 25000


def test_deposit_modal_strips_whitespace() -> None:
    m = DepositModalInput(
        char_name="  Malesyrup  ",
        realm="  Stormrage  ",
        region="EU",
        faction="Horde",
        amount=50000,
    )
    assert m.char_name == "Malesyrup"
    assert m.realm == "Stormrage"


# ---------------------------------------------------------------------------
# DepositModalInput — error paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_amount",
    [
        "50,000",          # comma separator rejected
        "50.000",          # period separator rejected
        "50 000",          # space separator rejected
        "50k",             # k suffix rejected
        "50K",
        "5m",
        "1b",
        "-100",            # signed rejected
        "+100",            # signed rejected
        "0",               # zero rejected (must be strictly positive)
        "abc",             # not a number
        "",                # empty
        "1e5",             # scientific notation rejected
        "0x100",           # hex rejected
    ],
)
def test_deposit_modal_rejects_malformed_amount(bad_amount: str) -> None:
    with pytest.raises(ValidationError) as info:
        DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region="EU", faction="Horde",
            amount=bad_amount,  # type: ignore[arg-type]
        )
    # The ValidationError must mention amount; the user-facing handler
    # surfaces the exact field that failed.
    assert "amount" in str(info.value).lower()


@pytest.mark.parametrize("region", ["XX", "ASIA", "us", "Europe", "OCE", ""])
def test_deposit_modal_rejects_invalid_region(region: str) -> None:
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region=region,  # type: ignore[arg-type]
            faction="Horde", amount=50000,
        )


@pytest.mark.parametrize("faction", ["Neutral", "Pirate", "X", ""])
def test_deposit_modal_rejects_invalid_faction(faction: str) -> None:
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region="EU",
            faction=faction,  # type: ignore[arg-type]
            amount=50000,
        )


@pytest.mark.parametrize(
    "bad_char",
    [
        "X",                  # too short
        "ABCDEFGHIJKLM",      # too long (13 chars)
        "Char1",              # contains digit
        "Char Name",          # space
        "Char_Name",          # underscore
        "Char-Name",          # dash
        "Char.Name",          # period
        "",                   # empty
    ],
)
def test_deposit_modal_rejects_invalid_char_name(bad_char: str) -> None:
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name=bad_char, realm="Stormrage",
            region="EU", faction="Horde", amount=50000,
        )


@pytest.mark.parametrize("good_char", ["Aleix", "Søren", "Aldéric", "Mîa", "Bj"])
def test_deposit_modal_accepts_unicode_letters(good_char: str) -> None:
    """Latin-1 supplement characters are valid in WoW char names."""
    m = DepositModalInput(
        char_name=good_char, realm="Stormrage",
        region="EU", faction="Horde", amount=50000,
    )
    assert m.char_name == good_char


def test_deposit_modal_rejects_realm_too_short() -> None:
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name="Malesyrup", realm="AB",
            region="EU", faction="Horde", amount=50000,
        )


def test_deposit_modal_rejects_realm_too_long() -> None:
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name="Malesyrup", realm="A" * 31,
            region="EU", faction="Horde", amount=50000,
        )


def test_deposit_modal_rejects_boolean_amount() -> None:
    """``True`` is technically an int subclass in Python; the validator
    rejects it explicitly so a buggy caller does not accidentally bet 1G."""
    with pytest.raises(ValidationError):
        DepositModalInput(
            char_name="Malesyrup", realm="Stormrage",
            region="EU", faction="Horde",
            amount=True,  # type: ignore[arg-type]
        )


def test_deposit_modal_is_frozen() -> None:
    m = DepositModalInput(
        char_name="Malesyrup", realm="Stormrage",
        region="EU", faction="Horde", amount=50000,
    )
    with pytest.raises(ValidationError):
        m.amount = 999  # type: ignore[misc]  # frozen


# ---------------------------------------------------------------------------
# WithdrawModalInput — same validators
# ---------------------------------------------------------------------------


def test_withdraw_modal_inherits_validators() -> None:
    """WithdrawModalInput inherits the modal-base validators verbatim."""
    m = WithdrawModalInput(
        char_name="Malesyrup", realm="Stormrage",
        region="eu", faction="horde", amount="30000",  # type: ignore[arg-type]
    )
    assert m.region == "EU"
    assert m.faction == "Horde"
    assert m.amount == 30000


# ---------------------------------------------------------------------------
# EditDynamicEmbedInput — admin-only modal validation
# ---------------------------------------------------------------------------


def test_edit_embed_normalises_color_hex() -> None:
    e = EditDynamicEmbedInput(title="Title", description="Desc", color_hex="f2b22a")
    assert e.color_hex == "#F2B22A"

    e2 = EditDynamicEmbedInput(title="Title", description="Desc", color_hex="#f2b22a")
    assert e2.color_hex == "#F2B22A"


@pytest.mark.parametrize("bad_color", ["red", "#xyzxyz", "#1234", "FFFFFF1", ""])
def test_edit_embed_rejects_bad_hex(bad_color: str) -> None:
    with pytest.raises(ValidationError):
        EditDynamicEmbedInput(title="T", description="D", color_hex=bad_color)


def test_edit_embed_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        EditDynamicEmbedInput(title="", description="D")


def test_edit_embed_rejects_oversized_description() -> None:
    with pytest.raises(ValidationError):
        EditDynamicEmbedInput(title="T", description="x" * 4001)


# ---------------------------------------------------------------------------
# Domain rows — construct from realistic payloads
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def test_user_snapshot_from_dict() -> None:
    payload = {
        "discord_id": 12345,
        "created_at": _now(),
        "updated_at": _now(),
        "banned": False,
        "banned_reason": None,
        "banned_at": None,
    }
    u = UserSnapshot.model_validate(payload)
    assert u.discord_id == 12345
    assert u.banned is False


def test_balance_snapshot_rejects_negative_balance() -> None:
    """Domain models defend the invariant even if the DB ever lied."""
    with pytest.raises(ValidationError):
        BalanceSnapshot.model_validate({
            "discord_id": 12345,
            "balance": -1,
            "locked_balance": 0,
            "total_wagered": 0,
            "total_won": 0,
            "updated_at": _now(),
            "version": 1,
        })


def test_deposit_ticket_full_payload() -> None:
    payload = {
        "id": 1,
        "ticket_uid": "deposit-1",
        "discord_id": 12345,
        "char_name": "Malesyrup",
        "realm": "Stormrage",
        "region": "EU",
        "faction": "Horde",
        "amount": 50000,
        "status": "open",
        "claimed_by": None,
        "claimed_at": None,
        "confirmed_at": None,
        "cancelled_at": None,
        "cancel_reason": None,
        "thread_id": 1234567890,
        "parent_channel_id": 9876543210,
        "expires_at": _now(),
        "last_activity_at": _now(),
        "created_at": _now(),
    }
    t = DepositTicket.model_validate(payload)
    assert t.status == "open"
    assert t.amount == 50000


def test_withdraw_ticket_with_fee_and_delivered() -> None:
    payload = {
        "id": 1,
        "ticket_uid": "withdraw-1",
        "discord_id": 12345,
        "char_name": "Malesyrup",
        "realm": "Stormrage",
        "region": "EU",
        "faction": "Horde",
        "amount": 30000,
        "fee": 600,
        "amount_delivered": 29400,
        "status": "confirmed",
        "claimed_by": 999,
        "claimed_at": _now(),
        "confirmed_at": _now(),
        "cancelled_at": None,
        "cancel_reason": None,
        "thread_id": 1234567890,
        "parent_channel_id": 9876543210,
        "expires_at": _now(),
        "last_activity_at": _now(),
        "created_at": _now(),
    }
    t = WithdrawTicket.model_validate(payload)
    assert t.fee == 600
    assert t.amount_delivered == 29400
    assert t.status == "confirmed"


def test_cashier_character_active_default() -> None:
    payload = {
        "id": 1,
        "discord_id": 999,
        "char_name": "Cashier",
        "realm": "Stormrage",
        "region": "EU",
        "faction": "Alliance",
        "is_active": True,
        "added_at": _now(),
        "removed_at": None,
    }
    c = CashierCharacter.model_validate(payload)
    assert c.is_active is True


@pytest.mark.parametrize("status", ["online", "offline", "break"])
def test_cashier_status_enum(status: str) -> None:
    payload = {
        "discord_id": 999,
        "status": status,
        "set_at": _now(),
        "auto_offline_at": None,
        "last_active_at": _now(),
    }
    s = CashierStatus.model_validate(payload)
    assert s.status == status


def test_cashier_status_rejects_invalid_enum() -> None:
    with pytest.raises(ValidationError):
        CashierStatus.model_validate({
            "discord_id": 999,
            "status": "afk",
            "set_at": _now(),
            "auto_offline_at": None,
            "last_active_at": _now(),
        })


def test_cashier_stats_rejects_negative_volume() -> None:
    with pytest.raises(ValidationError):
        CashierStats.model_validate({
            "discord_id": 999,
            "deposits_completed": 0,
            "deposits_cancelled": 0,
            "withdraws_completed": 0,
            "withdraws_cancelled": 0,
            "total_volume_g": -1,
            "total_online_seconds": 0,
            "avg_claim_to_confirm_s": None,
            "last_active_at": None,
            "updated_at": _now(),
        })


def test_dispute_full_payload() -> None:
    payload = {
        "id": 1,
        "ticket_type": "withdraw",
        "ticket_uid": "withdraw-1",
        "opener_id": 555,
        "opener_role": "admin",
        "reason": "user reports gold not received",
        "status": "open",
        "resolution": None,
        "resolved_by": None,
        "resolved_at": None,
        "opened_at": _now(),
    }
    d = Dispute.model_validate(payload)
    assert d.status == "open"
    assert d.opener_role == "admin"
