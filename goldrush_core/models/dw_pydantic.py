"""Pydantic v2 models for the Deposit/Withdraw bot.

Two flavours of models live here:

1. **Modal inputs** (e.g. ``DepositModalInput``) — what the user submits
   from a Discord modal. Discord modals always return strings; these
   models normalise and validate the strings into the strongly-typed
   shape the rest of the system expects:
       region:  case-insensitive 'eu' / 'EU' / 'Eu' → 'EU'
       faction: 'horde' / 'HORDE' / 'Horde' → 'Horde'
       amount:  '50000' → 50000  (rejects '50,000', '50k', '-1', '0')
   The hard format rules are enforced here so we can show a friendly
   message before any DB call. Range checks (min/max bet, balance check
   for withdraw) happen later because the bounds live in
   ``dw.global_config`` and can change without redeploy.

2. **Domain rows** (``DepositTicket``, ``WithdrawTicket``,
   ``CashierCharacter``, ``CashierStatus``, ``Dispute``,
   ``BalanceSnapshot``, ``UserSnapshot``) — frozen, validated
   representations of a row coming back from Postgres via asyncpg.
   They are constructed with ``Model.model_validate(dict(record))``
   inside the wrappers.

Both flavours use pydantic v2's strict mode where it matters and
``model_config = ConfigDict(frozen=True)`` so an instance never mutates
silently — important for code that passes a ticket through several
layers and expects the same data back at the end.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Allow upper-ASCII letters plus the common Latin-1 supplement range so
# names like 'Aldéric' or 'Søren' are accepted. Note: this regex
# matches ONLY letters — no digits, no punctuation, no spaces.
_CHAR_NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ]+$")

# Strict integer: one or more digits, no leading zero (so '007' is rejected),
# no signs, no separators, no suffix. Empty string is rejected by the regex.
_STRICT_INT_RE = re.compile(r"^(?:0|[1-9]\d*)$")


def _parse_strict_int(value: object, field_name: str) -> int:
    """Convert a string from a Discord modal into a strict non-negative int.

    Raises:
        ValueError: when the string contains separators (',' '.' ' '),
        suffixes ('k', 'm', 'b'), a sign, or is otherwise not a plain
        decimal number. Pass-through for ``int`` inputs (e.g. when this
        validator is reused on already-typed payloads).
    """
    if isinstance(value, bool):
        # In Python ``bool`` is a subclass of ``int`` — we reject it
        # explicitly so a True/False does not silently become 1/0.
        raise ValueError(f"{field_name} must be a number, not a boolean")
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or int, got {type(value).__name__}")
    stripped = value.strip()
    if not _STRICT_INT_RE.match(stripped):
        raise ValueError(
            f"{field_name} must be a non-negative integer with no separators "
            "or suffixes (examples: 100, 25000, 500000)"
        )
    return int(stripped)


# ---------------------------------------------------------------------------
# Common literal aliases
# ---------------------------------------------------------------------------

Region = Literal["EU", "NA"]
Faction = Literal["Alliance", "Horde"]
TicketStatus = Literal["open", "claimed", "confirmed", "cancelled", "expired", "disputed"]
DisputeStatus = Literal["open", "investigating", "resolved", "rejected"]
DisputeOpenerRole = Literal["admin", "user", "system"]
CashierStatusEnum = Literal["online", "offline", "break"]
TicketType = Literal["deposit", "withdraw"]


# ---------------------------------------------------------------------------
# Modal inputs — what comes back from a Discord modal submit
# ---------------------------------------------------------------------------


class _ModalBase(BaseModel):
    """Common validators reused by deposit and withdraw modal inputs.

    Pydantic v2 collects field_validator at class-creation time, so we
    define each validator as a classmethod that all subclasses inherit
    automatically. ``mode='before'`` runs before pydantic does its own
    type coercion — important for cases like 'eu' which we need to
    upper-case BEFORE pydantic checks the Literal.
    """

    char_name: str
    realm: str
    region: Region
    faction: Faction
    amount: int

    model_config = ConfigDict(
        frozen=True,
        # Strict typing for everything except the fields we explicitly
        # parse from strings via validators above.
        str_strip_whitespace=True,
    )

    @field_validator("char_name")
    @classmethod
    def _validate_char_name(cls, value: str) -> str:
        if not (2 <= len(value) <= 12):
            raise ValueError("character name must be 2-12 characters")
        if not _CHAR_NAME_RE.match(value):
            raise ValueError(
                "character name must contain only letters (no digits, no spaces, no punctuation)"
            )
        return value

    @field_validator("realm")
    @classmethod
    def _validate_realm(cls, value: str) -> str:
        if not (3 <= len(value) <= 30):
            raise ValueError("realm must be 3-30 characters")
        return value

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("faction", mode="before")
    @classmethod
    def _normalize_faction(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip().lower()
            # title() would yield 'Horde' / 'Alliance' which are exactly
            # the two literal values; normalise explicitly so 'HORDE',
            # 'horde', 'Horde' and 'hORDe' all resolve correctly.
            if stripped in ("alliance", "horde"):
                return stripped.title()
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: object) -> int:
        parsed = _parse_strict_int(value, "amount")
        if parsed <= 0:
            raise ValueError("amount must be strictly positive")
        # Sanity ceiling: nothing in the system can ever legitimately
        # ask for more than 10**18 G. The configured min/max from
        # dw.global_config will further constrain at the SECURITY
        # DEFINER level; this is just a final guard against absurd
        # input.
        if parsed > 10**18:
            raise ValueError("amount too large")
        return parsed


class DepositModalInput(_ModalBase):
    """Deposit modal submission. Range is enforced by the SECURITY DEFINER fn."""


class WithdrawModalInput(_ModalBase):
    """Withdraw modal submission. Same shape as deposit; range and balance
    sufficiency are checked at the DB layer (the function raises
    ``insufficient_balance`` when the user does not have enough)."""


class EditDynamicEmbedInput(BaseModel):
    """Input from ``/admin set-deposit-guide`` or ``/admin set-withdraw-guide``.

    The ``fields`` JSON is parsed lazily because Discord modals only
    accept strings. We keep it as a string here and parse downstream so
    the failure message can mention the exact JSON path.
    """

    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4000)
    color_hex: str | None = None
    fields_json: str | None = None
    image_url: str | None = None
    footer_text: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    @field_validator("color_hex")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lstrip("#")
        if not re.match(r"^[0-9A-Fa-f]{6}$", cleaned):
            raise ValueError("color_hex must be a 6-character hex value, optionally prefixed with #")
        return f"#{cleaned.upper()}"


# ---------------------------------------------------------------------------
# Domain rows — frozen representations of Postgres rows
# ---------------------------------------------------------------------------


class _DomainBase(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class UserSnapshot(_DomainBase):
    """A row from ``core.users``."""

    discord_id: int
    created_at: datetime
    updated_at: datetime
    banned: bool
    banned_reason: str | None = None
    banned_at: datetime | None = None


class BalanceSnapshot(_DomainBase):
    """A row from ``core.balances`` taken at a point in time.

    Notes:
    - ``balance`` is the spendable amount.
    - ``locked_balance`` is gold reserved by an in-flight withdraw.
    - ``total_wagered`` and ``total_won`` are denormalised aggregates.
    """

    discord_id: int
    balance: int = Field(ge=0)
    locked_balance: int = Field(ge=0)
    total_wagered: int = Field(ge=0)
    total_won: int = Field(ge=0)
    updated_at: datetime
    version: int


class DepositTicket(_DomainBase):
    """A row from ``dw.deposit_tickets``."""

    id: int
    ticket_uid: str
    discord_id: int
    char_name: str
    realm: str
    region: Region
    faction: Faction
    amount: int = Field(gt=0)
    status: TicketStatus
    claimed_by: int | None = None
    claimed_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    thread_id: int  # legacy name; under the channels-not-threads design carries the channel id
    parent_channel_id: int
    expires_at: datetime
    last_activity_at: datetime
    created_at: datetime


class WithdrawTicket(_DomainBase):
    """A row from ``dw.withdraw_tickets``.

    ``fee`` is captured at creation time and ``amount_delivered`` is set
    on confirm to ``amount - fee``. Both stay on the row forever for
    audit and receipt rendering.
    """

    id: int
    ticket_uid: str
    discord_id: int
    char_name: str
    realm: str
    region: Region
    faction: Faction
    amount: int = Field(gt=0)
    fee: int = Field(ge=0)
    amount_delivered: int | None = None
    status: TicketStatus
    claimed_by: int | None = None
    claimed_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    thread_id: int
    parent_channel_id: int
    expires_at: datetime
    last_activity_at: datetime
    created_at: datetime


class CashierCharacter(_DomainBase):
    """A row from ``dw.cashier_characters``."""

    id: int
    discord_id: int
    char_name: str
    realm: str
    region: Region
    faction: Faction
    is_active: bool
    added_at: datetime
    removed_at: datetime | None = None


class CashierStatus(_DomainBase):
    """A row from ``dw.cashier_status``."""

    discord_id: int
    status: CashierStatusEnum
    set_at: datetime
    auto_offline_at: datetime | None = None
    last_active_at: datetime


class CashierStats(_DomainBase):
    """A row from ``dw.cashier_stats``."""

    discord_id: int
    deposits_completed: int = Field(ge=0)
    deposits_cancelled: int = Field(ge=0)
    withdraws_completed: int = Field(ge=0)
    withdraws_cancelled: int = Field(ge=0)
    total_volume_g: int = Field(ge=0)
    total_online_seconds: int = Field(ge=0)
    avg_claim_to_confirm_s: int | None = None
    last_active_at: datetime | None = None
    updated_at: datetime


class Dispute(_DomainBase):
    """A row from ``dw.disputes``."""

    id: int
    ticket_type: TicketType
    ticket_uid: str
    opener_id: int
    opener_role: DisputeOpenerRole
    reason: str
    status: DisputeStatus
    resolution: str | None = None
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    opened_at: datetime
