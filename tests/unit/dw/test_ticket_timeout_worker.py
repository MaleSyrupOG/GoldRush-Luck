"""Unit tests for the ticket timeout worker (Story 8.1).

Story 8.1 says: every 60 s, find tickets with
``status IN ('open','claimed') AND expires_at < NOW()`` in BOTH
``dw.deposit_tickets`` and ``dw.withdraw_tickets`` and cancel each
via the corresponding SECURITY DEFINER fn (``dw.cancel_deposit`` /
``dw.cancel_withdraw``). For ``status='claimed'`` tickets, ALSO post
an admin alert.

The cancel SDFs are idempotent on already-terminal tickets (they
raise ``ticket_already_terminal`` which the worker swallows) so the
worker safely retries after a crash.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from goldrush_deposit_withdraw.workers.ticket_timeout import tick


# ---------------------------------------------------------------------------
# Pool fake — captures SQL the worker emits and the wrappers it calls.
# ---------------------------------------------------------------------------


class _FakePool:
    """Fake pool seeded with rows for the SELECT, capturing the cancels."""

    def __init__(
        self,
        *,
        expired_deposits: list[dict[str, Any]] | None = None,
        expired_withdraws: list[dict[str, Any]] | None = None,
        cancel_raises: dict[str, Exception] | None = None,
    ) -> None:
        self._expired_deposits = expired_deposits or []
        self._expired_withdraws = expired_withdraws or []
        self._cancel_raises = cancel_raises or {}
        self.fetch_queries: list[str] = []
        self.cancel_calls: list[tuple[str, tuple[Any, ...]]] = []
        # Track audit poster channel resolution. The worker calls
        # resolve_channel_id which fetchrows global_config; default to None
        # so the audit-log post is skipped in unit tests.
        self.audit_log_channel_id: int | None = None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_queries.append(query)
        if "deposit_tickets" in query:
            return self._expired_deposits
        if "withdraw_tickets" in query:
            return self._expired_withdraws
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        # Used by resolve_channel_id; return None so the audit poster
        # short-circuits (we test poster behaviour in audit_log tests).
        if "global_config" in query:
            return (
                {"value_int": self.audit_log_channel_id}
                if self.audit_log_channel_id is not None
                else None
            )
        return None

    async def execute(self, query: str, *args: Any) -> str:
        # cancel_deposit / cancel_withdraw both go through ``execute``.
        # Track which cancel was invoked.
        if "dw.cancel_deposit" in query:
            self.cancel_calls.append(("cancel_deposit", args))
            if "cancel_deposit" in self._cancel_raises:
                raise self._cancel_raises["cancel_deposit"]
        elif "dw.cancel_withdraw" in query:
            self.cancel_calls.append(("cancel_withdraw", args))
            if "cancel_withdraw" in self._cancel_raises:
                raise self._cancel_raises["cancel_withdraw"]
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        # cancel_withdraw is wrapped via fetchval (returns new balance).
        if "dw.cancel_withdraw" in query:
            self.cancel_calls.append(("cancel_withdraw", args))
            if "cancel_withdraw" in self._cancel_raises:
                raise self._cancel_raises["cancel_withdraw"]
            return 0
        return None


class _FakeBot:
    """Minimal bot — only used by the audit poster fallback path."""

    def get_channel(self, channel_id: int) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# tick — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_no_expired_tickets_does_nothing() -> None:
    pool = _FakePool()
    bot = _FakeBot()

    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert cancelled == 0
    assert pool.cancel_calls == []


@pytest.mark.asyncio
async def test_tick_cancels_expired_open_deposit() -> None:
    pool = _FakePool(
        expired_deposits=[
            {"ticket_uid": "deposit-12", "status": "open", "discord_id": 222, "amount": 50_000},
        ],
    )
    bot = _FakeBot()

    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert cancelled == 1
    assert len(pool.cancel_calls) == 1
    name, args = pool.cancel_calls[0]
    assert name == "cancel_deposit"
    # Reason carries 'expired' so the audit log explains the actor=0 row.
    assert "expired" in args[2].lower()
    # Actor id 0 == system / migration sentinel.
    assert args[1] == 0


@pytest.mark.asyncio
async def test_tick_cancels_expired_open_withdraw() -> None:
    pool = _FakePool(
        expired_withdraws=[
            {"ticket_uid": "withdraw-3", "status": "open", "discord_id": 333, "amount": 75_000},
        ],
    )
    bot = _FakeBot()

    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert cancelled == 1
    name, args = pool.cancel_calls[0]
    assert name == "cancel_withdraw"
    assert "expired" in args[2].lower()


@pytest.mark.asyncio
async def test_tick_cancels_expired_claimed_ticket_and_increments_count() -> None:
    """A ``claimed`` ticket times out too — same SDF handles cancel +
    refund (for withdraw). Story 8.1 AC: claimed-side cancellations
    also surface to admins via the audit-log channel poster."""
    pool = _FakePool(
        expired_deposits=[
            {"ticket_uid": "deposit-99", "status": "claimed", "discord_id": 222, "amount": 100_000},
        ],
    )
    bot = _FakeBot()

    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert cancelled == 1
    assert pool.cancel_calls[0][0] == "cancel_deposit"


@pytest.mark.asyncio
async def test_tick_swallows_already_terminal_idempotency() -> None:
    """If a concurrent worker (or admin force-cancel) already cancelled
    the ticket, the SDF raises ``ticket_already_terminal``. The worker
    swallows this — it's already in the desired terminal state."""
    pool = _FakePool(
        expired_deposits=[
            {"ticket_uid": "deposit-12", "status": "open", "discord_id": 222, "amount": 50_000},
        ],
        cancel_raises={"cancel_deposit": asyncpg.RaiseError("ticket_already_terminal (cancelled)")},
    )
    bot = _FakeBot()

    # Should not propagate.
    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]
    # Already terminal — count it as 0 because nothing changed.
    assert cancelled == 0


@pytest.mark.asyncio
async def test_tick_handles_mixed_deposit_and_withdraw_in_one_pass() -> None:
    pool = _FakePool(
        expired_deposits=[
            {"ticket_uid": "deposit-1", "status": "open", "discord_id": 222, "amount": 1_000},
        ],
        expired_withdraws=[
            {"ticket_uid": "withdraw-1", "status": "claimed", "discord_id": 333, "amount": 5_000},
            {"ticket_uid": "withdraw-2", "status": "open", "discord_id": 444, "amount": 2_000},
        ],
    )
    bot = _FakeBot()

    cancelled = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert cancelled == 3
    assert {c[0] for c in pool.cancel_calls} == {"cancel_deposit", "cancel_withdraw"}
