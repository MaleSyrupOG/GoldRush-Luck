"""Typed Python exceptions for the balance layer.

Every SECURITY DEFINER function in `dw.*` and `core.*` raises
``RAISE EXCEPTION '<sentinel>'`` with a sentinel string in the message.
The wrappers in ``goldrush_core.balance.dw_manager`` catch the asyncpg
``RaiseError`` they produce and translate it into one of the typed
exceptions below using ``translate_pg_error``.

The sentinels are stable function-internal contracts: any change to them
is a breaking change at the SQL/Python boundary and must come with both
sides updated. Keep this map in sync with the ``RAISE EXCEPTION`` calls
in ``ops/alembic/versions/20260501_*.py``.
"""

from __future__ import annotations

import asyncpg


class BalanceError(Exception):
    """Base class for every error raised by the balance layer.

    Carries the original Postgres message so callers can log it under a
    correlation ID without leaking secrets (no SECURITY DEFINER function
    interpolates a secret into the exception message).
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Specific exception classes — one per sentinel raised by the SECURITY
# DEFINER functions. Names mirror the spec wherever possible.
# ---------------------------------------------------------------------------


class InsufficientBalance(BalanceError):
    """User does not have enough balance for the requested operation."""


class InsufficientTreasury(BalanceError):
    """Treasury (discord_id=0) does not have enough balance for a sweep or transfer."""


class UserNotRegistered(BalanceError):
    """The Discord user has no row in core.users yet."""


class UserBanned(BalanceError):
    """The user is currently banned (core.users.banned = TRUE)."""


class TicketNotFound(BalanceError):
    """No ticket with the given uid exists."""


class TicketAlreadyClaimed(BalanceError):
    """A claim was attempted on a ticket whose status is not 'open'."""


class TicketNotClaimed(BalanceError):
    """A confirm/release was attempted on a ticket whose status is not 'claimed'."""


class TicketAlreadyTerminal(BalanceError):
    """A cancel was attempted on a ticket already in a terminal state."""


class WrongCashier(BalanceError):
    """Confirm/release/cancel attempted by a cashier other than the claimer."""


class RegionMismatch(BalanceError):
    """The cashier has no active char in the ticket's region."""


class AmountOutOfRange(BalanceError):
    """Bet amount falls outside the configured min/max."""


class InvalidRegion(BalanceError):
    """Region value not in {EU, NA}."""


class InvalidFaction(BalanceError):
    """Faction value not in {Alliance, Horde}."""


class InvalidStatus(BalanceError):
    """Status value not in the allowed set for that table."""


class InvalidTicketType(BalanceError):
    """Ticket type not in {deposit, withdraw}."""


class GlobalConfigMissing(BalanceError):
    """A required key is absent from dw.global_config."""


class InvariantViolation(BalanceError):
    """A SECURITY DEFINER function detected a state that should never occur."""


class CharacterNotFoundOrAlreadyRemoved(BalanceError):
    """remove_cashier_character could not find the row."""


class DuplicateIdempotency(BalanceError):
    """The same idempotency key was used a second time with different inputs."""


class CannotBanTreasury(BalanceError):
    """The treasury account (discord_id=0) cannot be banned."""


class CannotWithdrawToTreasurySelf(BalanceError):
    """treasury_withdraw_to_user called with target_user = 0."""


class TreasuryRowMissing(BalanceError):
    """Treasury seed row missing (core.balances WHERE discord_id=0)."""


class AmountMustBePositive(BalanceError):
    """Treasury operations require strictly-positive amounts."""


class PartialRefundRequiresPositiveAmount(BalanceError):
    """resolve_dispute(action='partial-refund') was called without an amount."""


class RefundFullOnlyForWithdrawDisputes(BalanceError):
    """resolve_dispute(action='refund-full') called on a deposit dispute."""


class DisputeNotFound(BalanceError):
    """No dispute with the given id."""


class DisputeAlreadyTerminal(BalanceError):
    """resolve_dispute called on an already-terminal dispute."""


class InvalidAction(BalanceError):
    """resolve_dispute received an action outside the allowed set."""


class InvalidOpenerRole(BalanceError):
    """open_dispute received an opener_role outside the allowed set."""


class CashierNotOnline(BalanceError):
    """expire_cashier called on a cashier whose status is not 'online'.

    The cashier-idle worker swallows this — concurrent ``/cashier-offline``
    or another worker iteration may have moved the row to the desired
    state already.
    """


# ---------------------------------------------------------------------------
# Translation table: sentinel substring -> exception class.
#
# Order matters because `'ticket_already_terminal'` is a substring of
# nothing else, and `'ticket_not_found'` of nothing else, but
# `'invariant_violation'` and `'invariant_violation_locked_too_low'` would
# overlap if we used `in`. We keep substrings unique by using the most
# specific marker the SECURITY DEFINER functions raise.
# ---------------------------------------------------------------------------


_ERROR_TABLE: tuple[tuple[str, type[BalanceError]], ...] = (
    ("invariant_violation_locked_too_low", InvariantViolation),
    ("partial_refund_requires_positive_amount", PartialRefundRequiresPositiveAmount),
    ("refund_full_only_for_withdraw_disputes", RefundFullOnlyForWithdrawDisputes),
    ("character_not_found_or_already_removed", CharacterNotFoundOrAlreadyRemoved),
    ("cannot_withdraw_to_treasury_self", CannotWithdrawToTreasurySelf),
    ("amount_must_be_positive", AmountMustBePositive),
    ("ticket_already_terminal", TicketAlreadyTerminal),
    ("dispute_already_terminal", DisputeAlreadyTerminal),
    ("ticket_not_claimed", TicketNotClaimed),
    ("ticket_not_found", TicketNotFound),
    ("dispute_not_found", DisputeNotFound),
    ("treasury_row_missing", TreasuryRowMissing),
    ("user_not_registered", UserNotRegistered),
    ("invalid_ticket_type", InvalidTicketType),
    ("invalid_opener_role", InvalidOpenerRole),
    ("amount_out_of_range", AmountOutOfRange),
    ("global_config missing", GlobalConfigMissing),
    ("insufficient_treasury", InsufficientTreasury),
    ("insufficient_balance", InsufficientBalance),
    ("cannot_ban_treasury", CannotBanTreasury),
    ("region_mismatch", RegionMismatch),
    ("already_claimed", TicketAlreadyClaimed),
    ("invalid_faction", InvalidFaction),
    ("invalid_region", InvalidRegion),
    ("invalid_status", InvalidStatus),
    ("invalid_action", InvalidAction),
    ("wrong_cashier", WrongCashier),
    ("user_banned", UserBanned),
    ("cashier_not_online", CashierNotOnline),
)


def translate_pg_error(exc: asyncpg.PostgresError) -> BalanceError:
    """Translate an asyncpg.PostgresError into a typed BalanceError.

    Substring matching against the message is brittle but acceptable here:
    every sentinel is a unique snake_case token raised by exactly one
    SECURITY DEFINER function, and the migration files are the source of
    truth — a pre-merge test (in CI, future story) can grep both this
    file and the migrations to catch drift.

    If no sentinel matches, a generic BalanceError preserving the original
    message is returned. This is rare and indicates either an undocumented
    raise path in a SECURITY DEFINER function or a real Postgres engine
    error (deadlock, constraint violation by a non-SECURITY-DEFINER call,
    etc.).
    """
    message = str(exc)
    lowered = message.lower()
    for sentinel, cls in _ERROR_TABLE:
        if sentinel in lowered:
            return cls(message)
    return BalanceError(message)
