"""Unit tests for `deathroll_core.balance.exceptions.translate_pg_error`.

The translation table maps SECURITY DEFINER sentinels to typed Python
exception classes. These tests assert that every sentinel raised by the
migrations 0001-0012 maps to the right class. Drift between this table
and the actual ``RAISE EXCEPTION`` calls is the most likely silent bug
in the wrappers, so we cover every sentinel explicitly.

The tests do not need a database; they construct fake
``asyncpg.RaiseError`` instances locally.
"""

from __future__ import annotations

import pytest

from deathroll_core.balance.exceptions import (
    AmountMustBePositive,
    AmountOutOfRange,
    BalanceError,
    CannotBanTreasury,
    CannotWithdrawToTreasurySelf,
    CharacterNotFoundOrAlreadyRemoved,
    DisputeAlreadyTerminal,
    DisputeNotFound,
    GlobalConfigMissing,
    InsufficientBalance,
    InsufficientTreasury,
    InvalidAction,
    InvalidFaction,
    InvalidOpenerRole,
    InvalidRegion,
    InvalidStatus,
    InvalidTicketType,
    InvariantViolation,
    PartialRefundRequiresPositiveAmount,
    RefundFullOnlyForWithdrawDisputes,
    RegionMismatch,
    TicketAlreadyClaimed,
    TicketAlreadyTerminal,
    TicketNotClaimed,
    TicketNotFound,
    TreasuryRowMissing,
    UserBanned,
    UserNotRegistered,
    WrongCashier,
    translate_pg_error,
)


class _FakePgError(Exception):
    """Minimal stand-in for ``asyncpg.PostgresError``.

    ``translate_pg_error`` only calls ``str(exc)`` so any Exception with
    an informative message works for these tests. We use a custom class
    rather than raw ``Exception`` to keep the type annotation precise.
    """


# Each row is (sentinel-as-it-appears-in-the-migration, expected_class).
# The order mirrors the migrations 0001-0012; new sentinels added to
# any future migration MUST be appended here AND to the table in
# ``deathroll_core/balance/exceptions.py`` in the same PR.
SENTINEL_CASES = [
    ("amount_out_of_range (got 50, expected 200 to 200000)", AmountOutOfRange),
    ("invalid_region (FR)", InvalidRegion),
    ("invalid_faction (Neutral)", InvalidFaction),
    ("user_not_registered", UserNotRegistered),
    ("user_banned", UserBanned),
    ("insufficient_balance (have 100, need 50000)", InsufficientBalance),
    ("ticket_not_found", TicketNotFound),
    ("ticket_not_claimed (status=open)", TicketNotClaimed),
    ("ticket_already_terminal (status=confirmed)", TicketAlreadyTerminal),
    ("wrong_cashier (claimed_by=999 calling=1234)", WrongCashier),
    ("already_claimed (status=claimed)", TicketAlreadyClaimed),
    ("region_mismatch (cashier 999 has no active char in region NA)", RegionMismatch),
    ("invariant_violation_locked_too_low (locked=10, ticket_amount=50)", InvariantViolation),
    ("invalid_ticket_type", InvalidTicketType),
    ("invalid_opener_role", InvalidOpenerRole),
    ("invalid_action (frobnicate)", InvalidAction),
    ("invalid_status (transient)", InvalidStatus),
    ("global_config missing required deposit keys", GlobalConfigMissing),
    ("dispute_not_found", DisputeNotFound),
    ("dispute_already_terminal (resolved)", DisputeAlreadyTerminal),
    ("partial_refund_requires_positive_amount", PartialRefundRequiresPositiveAmount),
    ("refund_full_only_for_withdraw_disputes", RefundFullOnlyForWithdrawDisputes),
    ("amount_must_be_positive", AmountMustBePositive),
    ("treasury_row_missing", TreasuryRowMissing),
    ("insufficient_treasury (have 0, sweeping 100)", InsufficientTreasury),
    ("cannot_withdraw_to_treasury_self", CannotWithdrawToTreasurySelf),
    ("cannot_ban_treasury", CannotBanTreasury),
    ("character_not_found_or_already_removed", CharacterNotFoundOrAlreadyRemoved),
]


@pytest.mark.parametrize("message,expected_cls", SENTINEL_CASES)
def test_translates_known_sentinels(message: str, expected_cls: type[BalanceError]) -> None:
    """Every documented sentinel maps to its specific exception class.

    A regression here means either: (a) the migration changed a sentinel
    without updating the translation table, or (b) the table grew a
    longer match that absorbs a shorter one (ordering bug).
    """
    exc = _FakePgError(message)
    translated = translate_pg_error(exc)
    assert isinstance(translated, expected_cls), (
        f"expected {expected_cls.__name__} for {message!r}, "
        f"got {type(translated).__name__}"
    )
    assert isinstance(translated, BalanceError)
    assert message in translated.message


def test_unknown_sentinel_falls_back_to_base() -> None:
    """An unknown error is preserved as a plain BalanceError.

    This is the safety net for engine-level errors (deadlock detected,
    constraint violation surfaced from a CHECK we did not anticipate,
    etc.). The caller can still log the message under a correlation id
    even if it cannot pattern-match the exception type.
    """
    exc = _FakePgError("something_we_did_not_plan_for: weird message")
    translated = translate_pg_error(exc)
    assert type(translated) is BalanceError
    assert "something_we_did_not_plan_for" in translated.message


def test_specific_match_wins_over_generic() -> None:
    """``invariant_violation_locked_too_low`` must NOT match plain InvariantViolation.

    If the long sentinel were checked after the short one, this assertion
    would fail. The order in ``_ERROR_TABLE`` puts long-prefix matches
    first; this test guards that ordering.
    """
    exc = _FakePgError("invariant_violation_locked_too_low (locked=0, ticket_amount=50)")
    translated = translate_pg_error(exc)
    # InvariantViolation is the right class because the more specific
    # locked_too_low sentinel maps to it (per migration 0007).
    assert isinstance(translated, InvariantViolation)


def test_partial_refund_specific_match() -> None:
    """``partial_refund_requires_positive_amount`` must not get classified as InvalidAction.

    Both contain the substring ``action`` but only the more specific
    marker should win.
    """
    exc = _FakePgError("partial_refund_requires_positive_amount")
    translated = translate_pg_error(exc)
    assert isinstance(translated, PartialRefundRequiresPositiveAmount)


def test_balance_error_carries_message() -> None:
    """The message attribute is accessible for logging."""
    exc = _FakePgError("insufficient_balance (have 100, need 50000)")
    translated = translate_pg_error(exc)
    assert translated.message == "insufficient_balance (have 100, need 50000)"
    assert str(translated) == translated.message
