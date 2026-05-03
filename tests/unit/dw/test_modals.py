"""Unit tests for the 2FA confirmation modal (Story 5.5 / 6.4 foundation).

The modal is presented to the cashier on ``/confirm`` (and to admins
on every treasury operation). Spec §5.5: the user must type the
exact magic word — typing "confirm" lowercase is rejected, "CONFIRM"
is accepted.

Tests target the validator alone — Discord's modal lifecycle is
exercised in Epic 14 integration tests.
"""

from __future__ import annotations

import pytest
from deathroll_deposit_withdraw.views.modals import is_magic_word_match


@pytest.mark.parametrize(
    "supplied,expected",
    [
        ("CONFIRM", True),
        ("confirm", False),
        ("Confirm", False),
        (" CONFIRM", True),    # leading whitespace is forgiven
        ("CONFIRM ", True),    # trailing whitespace is forgiven
        ("CONFIRM\n", True),   # newline is stripped
        ("CONFIRMED", False),  # extra text after the magic word
        ("confirm CONFIRM", False),
        ("", False),
    ],
)
def test_magic_word_match_canonical_cases(supplied: str, expected: bool) -> None:
    assert is_magic_word_match(supplied=supplied, expected="CONFIRM") is expected


def test_custom_magic_words_supported() -> None:
    """Treasury sweep uses ``SWEEP``; treasury withdraw-to-user uses
    ``TREASURY-WITHDRAW``. The validator is generic."""
    assert is_magic_word_match(supplied="SWEEP", expected="SWEEP") is True
    assert is_magic_word_match(supplied="sweep", expected="SWEEP") is False
    assert (
        is_magic_word_match(supplied="TREASURY-WITHDRAW", expected="TREASURY-WITHDRAW") is True
    )
