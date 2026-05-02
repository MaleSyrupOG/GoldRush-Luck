"""Typed-result orchestration around the SECURITY DEFINER ticket fns.

The wrappers in ``goldrush_core.balance.dw_manager`` translate
Postgres ``RaiseError`` into typed Python exceptions. That's the
right layer for pluming, but in the cog handler we'd rather
``match`` over a typed result than chain ``except`` clauses — it
keeps the deposit / withdraw command paths flat and easy to read.

These functions take the SECURITY DEFINER call inputs plus the
already-validated pydantic payload, run the call, and return one
of the per-flow ``Outcome.*`` variants. The cog dispatches to the
right embed / ephemeral message based on the variant.

Discord-side concerns (rate-limiting, channel binding, thread
creation) live in the cog — they can't be expressed cleanly here
because Discord state is per-interaction. The orchestration is
the seam where DB-side concerns end and Discord-side begin.
"""

from __future__ import annotations

from dataclasses import dataclass

from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    apply_deposit_ticket,
    apply_withdraw_ticket,
)
from goldrush_core.db import Executor
from goldrush_core.models.dw_pydantic import DepositModalInput, WithdrawModalInput

# ---------------------------------------------------------------------------
# Deposit outcome union
# ---------------------------------------------------------------------------


class DepositOutcome:
    """Namespace for the deposit-attempt variants."""

    @dataclass(frozen=True)
    class Success:
        ticket_uid: str

    @dataclass(frozen=True)
    class UserBanned:
        message: str = "user_banned"

    @dataclass(frozen=True)
    class AmountOutOfRange:
        message: str

    @dataclass(frozen=True)
    class InvalidInput:
        message: str

    @dataclass(frozen=True)
    class ConfigError:
        message: str

    @dataclass(frozen=True)
    class Unexpected:
        message: str


DepositResult = (
    DepositOutcome.Success
    | DepositOutcome.UserBanned
    | DepositOutcome.AmountOutOfRange
    | DepositOutcome.InvalidInput
    | DepositOutcome.ConfigError
    | DepositOutcome.Unexpected
)


async def open_deposit_ticket(
    *,
    pool: Executor,
    payload: DepositModalInput,
    discord_id: int,
    thread_id: int,
    parent_channel_id: int,
) -> DepositResult:
    """Attempt to open a deposit ticket; return a typed outcome.

    The thread MUST be created by the caller before this is invoked
    because ``dw.create_deposit_ticket`` requires both ``thread_id``
    and ``parent_channel_id`` (NOT NULL columns). On any failure
    the caller is responsible for tearing the thread back down.
    """
    try:
        uid = await apply_deposit_ticket(
            pool,
            discord_id=discord_id,
            char_name=payload.char_name,
            realm=payload.realm,
            region=payload.region,
            faction=payload.faction,
            amount=payload.amount,
            thread_id=thread_id,
            parent_channel_id=parent_channel_id,
        )
        return DepositOutcome.Success(ticket_uid=uid)
    except exc.UserBanned as e:
        return DepositOutcome.UserBanned(message=e.message)
    except exc.AmountOutOfRange as e:
        return DepositOutcome.AmountOutOfRange(message=e.message)
    except (exc.InvalidRegion, exc.InvalidFaction) as e:
        return DepositOutcome.InvalidInput(message=e.message)
    except exc.GlobalConfigMissing as e:
        return DepositOutcome.ConfigError(message=e.message)
    except exc.BalanceError as e:
        return DepositOutcome.Unexpected(message=e.message)


# ---------------------------------------------------------------------------
# Withdraw outcome union
# ---------------------------------------------------------------------------


class WithdrawOutcome:
    """Namespace for the withdraw-attempt variants."""

    @dataclass(frozen=True)
    class Success:
        ticket_uid: str

    @dataclass(frozen=True)
    class UserBanned:
        message: str = "user_banned"

    @dataclass(frozen=True)
    class UserNotRegistered:
        message: str = "user_not_registered"

    @dataclass(frozen=True)
    class InsufficientBalance:
        message: str

    @dataclass(frozen=True)
    class AmountOutOfRange:
        message: str

    @dataclass(frozen=True)
    class InvalidInput:
        message: str

    @dataclass(frozen=True)
    class ConfigError:
        message: str

    @dataclass(frozen=True)
    class Unexpected:
        message: str


WithdrawResult = (
    WithdrawOutcome.Success
    | WithdrawOutcome.UserBanned
    | WithdrawOutcome.UserNotRegistered
    | WithdrawOutcome.InsufficientBalance
    | WithdrawOutcome.AmountOutOfRange
    | WithdrawOutcome.InvalidInput
    | WithdrawOutcome.ConfigError
    | WithdrawOutcome.Unexpected
)


async def open_withdraw_ticket(
    *,
    pool: Executor,
    payload: WithdrawModalInput,
    discord_id: int,
    thread_id: int,
    parent_channel_id: int,
) -> WithdrawResult:
    """Attempt to open a withdraw ticket; return a typed outcome.

    Withdraw has more failure modes than deposit because the
    SECURITY DEFINER fn also locks the user's balance — it can
    surface ``user_not_registered`` (no row in core.users) and
    ``insufficient_balance``.
    """
    try:
        uid = await apply_withdraw_ticket(
            pool,
            discord_id=discord_id,
            char_name=payload.char_name,
            realm=payload.realm,
            region=payload.region,
            faction=payload.faction,
            amount=payload.amount,
            thread_id=thread_id,
            parent_channel_id=parent_channel_id,
        )
        return WithdrawOutcome.Success(ticket_uid=uid)
    except exc.UserNotRegistered as e:
        return WithdrawOutcome.UserNotRegistered(message=e.message)
    except exc.UserBanned as e:
        return WithdrawOutcome.UserBanned(message=e.message)
    except exc.InsufficientBalance as e:
        return WithdrawOutcome.InsufficientBalance(message=e.message)
    except exc.AmountOutOfRange as e:
        return WithdrawOutcome.AmountOutOfRange(message=e.message)
    except (exc.InvalidRegion, exc.InvalidFaction) as e:
        return WithdrawOutcome.InvalidInput(message=e.message)
    except exc.GlobalConfigMissing as e:
        return WithdrawOutcome.ConfigError(message=e.message)
    except exc.BalanceError as e:
        return WithdrawOutcome.Unexpected(message=e.message)


__all__ = [
    "DepositOutcome",
    "DepositResult",
    "WithdrawOutcome",
    "WithdrawResult",
    "open_deposit_ticket",
    "open_withdraw_ticket",
]
