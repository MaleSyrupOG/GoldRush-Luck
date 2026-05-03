"""Tests for the treasury 2FA validators (Story 10.6).

Story 10.6 ships three admin slash commands. ``treasury-balance`` is
read-only; the other two (``treasury-sweep`` and
``treasury-withdraw-to-user``) gate the actual SDF call behind a
2FA modal demanding the operator re-type a magic word AND the
amount (and, for withdraws, the target user id).

Discord modals require an event loop to construct — heavy to set up
in unit tests — so the validation logic lives as PURE FUNCTIONS in
``goldrush_deposit_withdraw.views.modals``. These tests pin that
logic; the modal itself is a thin Discord wrapper.
"""

from __future__ import annotations

from goldrush_deposit_withdraw.views.modals import (
    validate_treasury_sweep_confirm,
    validate_treasury_withdraw_confirm,
)


# ---------------------------------------------------------------------------
# validate_treasury_sweep_confirm
# ---------------------------------------------------------------------------


def test_sweep_confirm_happy_path() -> None:
    err = validate_treasury_sweep_confirm(
        magic_word="SWEEP",
        amount_str="50000",
        expected_amount=50_000,
    )
    assert err is None


def test_sweep_confirm_rejects_lowercase_magic_word() -> None:
    err = validate_treasury_sweep_confirm(
        magic_word="sweep",
        amount_str="50000",
        expected_amount=50_000,
    )
    assert err is not None
    assert "SWEEP" in err  # the user-facing message names the expected word


def test_sweep_confirm_accepts_amount_with_thousands_separators() -> None:
    """Operators copy-paste numbers with commas/underscores from spreadsheets;
    the validator forgives those for ergonomics."""
    err = validate_treasury_sweep_confirm(
        magic_word="SWEEP",
        amount_str="50,000",
        expected_amount=50_000,
    )
    assert err is None
    err2 = validate_treasury_sweep_confirm(
        magic_word="SWEEP",
        amount_str="50_000",
        expected_amount=50_000,
    )
    assert err2 is None


def test_sweep_confirm_rejects_non_integer_amount() -> None:
    err = validate_treasury_sweep_confirm(
        magic_word="SWEEP",
        amount_str="fifty thousand",
        expected_amount=50_000,
    )
    assert err is not None


def test_sweep_confirm_rejects_amount_mismatch() -> None:
    err = validate_treasury_sweep_confirm(
        magic_word="SWEEP",
        amount_str="100000",
        expected_amount=50_000,
    )
    assert err is not None
    # Both sides should appear in the user-facing error so the operator
    # sees what they typed vs. what was expected.
    assert "100" in err
    assert "50" in err


# ---------------------------------------------------------------------------
# validate_treasury_withdraw_confirm
# ---------------------------------------------------------------------------


def test_withdraw_confirm_happy_path() -> None:
    err = validate_treasury_withdraw_confirm(
        magic_word="TREASURY-WITHDRAW",
        amount_str="25000",
        expected_amount=25_000,
        user_id_str="111111111111111111",
        expected_user_id=111_111_111_111_111_111,
    )
    assert err is None


def test_withdraw_confirm_rejects_wrong_magic_word() -> None:
    err = validate_treasury_withdraw_confirm(
        magic_word="WITHDRAW",  # missing the TREASURY- prefix
        amount_str="25000",
        expected_amount=25_000,
        user_id_str="111",
        expected_user_id=111,
    )
    assert err is not None


def test_withdraw_confirm_rejects_user_id_mismatch() -> None:
    err = validate_treasury_withdraw_confirm(
        magic_word="TREASURY-WITHDRAW",
        amount_str="25000",
        expected_amount=25_000,
        user_id_str="222",  # wrong user
        expected_user_id=111,
    )
    assert err is not None


def test_withdraw_confirm_rejects_non_numeric_user_id() -> None:
    err = validate_treasury_withdraw_confirm(
        magic_word="TREASURY-WITHDRAW",
        amount_str="25000",
        expected_amount=25_000,
        user_id_str="not-a-number",
        expected_user_id=111,
    )
    assert err is not None
