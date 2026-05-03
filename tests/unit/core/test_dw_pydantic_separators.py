"""Gap-fill tests for Story 14.7 — modal validation edge cases.

The bulk of modal validation coverage already lives in
``test_dw_pydantic.py`` (region / faction / char name / boolean
amount / etc.). This file adds the cases the Story 14.7 AC names
explicitly that weren't previously parametrized:

- ``DepositModal`` rejects amounts with thousand separators
  (``"1,000"``, ``"10_000"``) — ``_STRICT_INT_RE`` rejects them but
  the existing parametrize list missed those. Underscores are
  particularly tricky: Python's ``int("10_000")`` returns 10000
  thanks to PEP 515, so without our regex an underscore-bearing
  input would silently parse.

- ``EditDynamicEmbedInput`` accepts malformed JSON in ``fields_json``
  by design (downstream renderer falls back to ``[]``). The AC
  asks for rejection but the implemented behaviour is tolerant
  rendering — pinning that behaviour here so a future regression
  surfaces (we'd rather change the test than crash a live embed).
"""

from __future__ import annotations

import pytest
from deathroll_core.models.dw_pydantic import (
    DepositModalInput,
    EditDynamicEmbedInput,
    WithdrawModalInput,
)
from pydantic import ValidationError


@pytest.mark.parametrize(
    "bad_amount",
    [
        "1,000",
        "1.000",
        "10_000",
        "100_000_000",
        " 50 000 ",  # space-separated; whitespace strip leaves "50 000"
    ],
)
def test_deposit_modal_rejects_separators_in_amount(bad_amount: str) -> None:
    with pytest.raises(ValidationError) as info:
        DepositModalInput(
            char_name="Aleix",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=bad_amount,  # type: ignore[arg-type]
        )
    assert "amount" in str(info.value).lower()


@pytest.mark.parametrize(
    "bad_amount",
    [
        "1,000",
        "10_000",
    ],
)
def test_withdraw_modal_rejects_separators_in_amount(bad_amount: str) -> None:
    """WithdrawModal inherits the same _ModalBase validators."""
    with pytest.raises(ValidationError):
        WithdrawModalInput(
            char_name="Aleix",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=bad_amount,  # type: ignore[arg-type]
        )


def test_edit_dynamic_embed_input_accepts_malformed_fields_json() -> None:
    """Story 14.7 AC asks for rejection of malformed JSON in
    ``fields``. Implementation chose tolerance instead — the
    downstream embed renderer falls back to an empty fields list
    on JSONDecodeError, which means a copy-paste typo doesn't
    take the live #how-to-deposit message offline.

    Pinning the tolerant behaviour here so a future regression
    (e.g. "let's add strict validation") shows up as a test
    failure that forces a deliberate decision."""
    payload = EditDynamicEmbedInput(
        title="t",
        description="d",
        fields_json="this is not json {{ broken",
    )
    # Parse-time tolerance: the input is accepted as a string.
    assert payload.fields_json == "this is not json {{ broken"


def test_edit_dynamic_embed_input_accepts_valid_json_fields() -> None:
    """Sanity counterpart — the tolerant path doesn't accidentally
    strip valid JSON either."""
    payload = EditDynamicEmbedInput(
        title="t",
        description="d",
        fields_json='[{"name": "Step 1", "value": "Run /deposit"}]',
    )
    assert payload.fields_json is not None and "Step 1" in payload.fields_json
