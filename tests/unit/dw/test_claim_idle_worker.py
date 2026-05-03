"""Unit tests for the claim-idle worker (Story 8.2).

Story 8.2 says: every 60 s, walk both ticket tables for rows in
``status='claimed'`` and apply two distinct deadlines:

- ``last_activity_at < NOW() - 30 min`` → auto-release (back to
  ``open``, FIFO can re-claim) AND repost the cashier alert so the
  next cashier sees it.
- ``claimed_at < NOW() - 2 h`` → auto-cancel (cashier abandoned;
  refunds happen via ``cancel_withdraw`` for withdraw tickets).

Both cancel paths are idempotent — concurrent admin force-cancel /
force-release race the worker harmlessly because the SECURITY
DEFINER fns raise ``ticket_not_claimed`` / ``ticket_already_terminal``
which the worker swallows.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from goldrush_deposit_withdraw.workers.claim_idle import tick


class _FakePool:
    def __init__(
        self,
        *,
        idle_releases: list[dict[str, Any]] | None = None,
        long_claimed: list[dict[str, Any]] | None = None,
        cancel_raises: dict[str, Exception] | None = None,
        release_raises: Exception | None = None,
    ) -> None:
        # Two SELECT queries the worker emits — one per deadline.
        self._idle_releases = idle_releases or []
        self._long_claimed = long_claimed or []
        self._cancel_raises = cancel_raises or {}
        self._release_raises = release_raises
        self.fetch_queries: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_queries.append(query)
        # Disambiguate by deadline expression.
        if "claimed_at" in query and "INTERVAL '2" in query:
            return self._long_claimed
        if "last_activity_at" in query:
            return self._idle_releases
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        # Channel binding for cashier alerts: skip the post in tests.
        if "global_config" in query:
            return None
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "dw.release_ticket" in query:
            self.calls.append(("release_ticket", args))
            if self._release_raises is not None:
                raise self._release_raises
        elif "dw.cancel_deposit" in query:
            self.calls.append(("cancel_deposit", args))
            if "cancel_deposit" in self._cancel_raises:
                raise self._cancel_raises["cancel_deposit"]
        elif "dw.cancel_withdraw" in query:
            self.calls.append(("cancel_withdraw", args))
            if "cancel_withdraw" in self._cancel_raises:
                raise self._cancel_raises["cancel_withdraw"]
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "dw.cancel_withdraw" in query:
            self.calls.append(("cancel_withdraw", args))
            if "cancel_withdraw" in self._cancel_raises:
                raise self._cancel_raises["cancel_withdraw"]
            return 0
        return None


class _FakeBot:
    def get_channel(self, channel_id: int) -> None:
        return None


# ---------------------------------------------------------------------------
# Idle 30 min → auto-release + repost cashier alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_releases_idle_claimed_deposit() -> None:
    pool = _FakePool(
        idle_releases=[
            {
                "ticket_type": "deposit",
                "ticket_uid": "deposit-12",
                "thread_id": 555,
                "region": "EU",
                "faction": "Horde",
                "amount": 50_000,
                "claimed_by": 9001,
            }
        ]
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert summary.released == 1
    assert summary.cancelled == 0
    name, args = pool.calls[0]
    assert name == "release_ticket"
    # actor_id is the row's claimed_by (the original claimer), not the
    # system actor — required by the SDF's wrong_cashier guard.
    assert args == ("deposit", "deposit-12", 9001)


@pytest.mark.asyncio
async def test_tick_releases_idle_claimed_withdraw() -> None:
    pool = _FakePool(
        idle_releases=[
            {
                "ticket_type": "withdraw",
                "ticket_uid": "withdraw-3",
                "thread_id": 666,
                "region": "NA",
                "faction": "Alliance",
                "amount": 75_000,
                "claimed_by": 9002,
            }
        ]
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert summary.released == 1
    name, args = pool.calls[0]
    assert name == "release_ticket"
    assert args == ("withdraw", "withdraw-3", 9002)


@pytest.mark.asyncio
async def test_tick_swallows_release_already_open() -> None:
    """If a cashier voluntarily released the ticket between SELECT and
    the release fn, the SDF raises ``ticket_not_claimed``. The worker
    swallows it — desired state."""
    pool = _FakePool(
        idle_releases=[
            {
                "ticket_type": "deposit",
                "ticket_uid": "deposit-12",
                "thread_id": 555,
                "region": "EU",
                "faction": "Horde",
                "amount": 50_000,
                "claimed_by": 9001,
            }
        ],
        release_raises=asyncpg.RaiseError("ticket_not_claimed (status=open)"),
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    # No counted release because it was already in the desired state.
    assert summary.released == 0


# ---------------------------------------------------------------------------
# Claimed > 2 h → auto-cancel + refund (withdraw)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_cancels_long_claimed_deposit() -> None:
    pool = _FakePool(
        long_claimed=[
            {
                "ticket_type": "deposit",
                "ticket_uid": "deposit-99",
                "thread_id": 777,
                "region": "EU",
                "faction": "Horde",
                "amount": 100_000,
            }
        ]
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert summary.cancelled == 1
    name, args = pool.calls[0]
    assert name == "cancel_deposit"
    assert args[1] == 0  # system actor
    assert "abandon" in args[2].lower() or "claim" in args[2].lower()


@pytest.mark.asyncio
async def test_tick_cancels_long_claimed_withdraw_and_refunds() -> None:
    pool = _FakePool(
        long_claimed=[
            {
                "ticket_type": "withdraw",
                "ticket_uid": "withdraw-99",
                "thread_id": 888,
                "region": "NA",
                "faction": "Alliance",
                "amount": 25_000,
            }
        ]
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]

    assert summary.cancelled == 1
    name, _args = pool.calls[0]
    assert name == "cancel_withdraw"


@pytest.mark.asyncio
async def test_tick_swallows_already_terminal_on_cancel() -> None:
    pool = _FakePool(
        long_claimed=[
            {
                "ticket_type": "deposit",
                "ticket_uid": "deposit-99",
                "thread_id": 777,
                "region": "EU",
                "faction": "Horde",
                "amount": 100_000,
            }
        ],
        cancel_raises={
            "cancel_deposit": asyncpg.RaiseError(
                "ticket_already_terminal (cancelled)"
            )
        },
    )
    bot = _FakeBot()

    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]
    assert summary.cancelled == 0


# ---------------------------------------------------------------------------
# Mixed pass + no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_handles_both_deadlines_in_one_pass() -> None:
    pool = _FakePool(
        idle_releases=[
            {
                "ticket_type": "deposit",
                "ticket_uid": "deposit-1",
                "thread_id": 1,
                "region": "EU",
                "faction": "Horde",
                "amount": 1_000,
                "claimed_by": 9001,
            }
        ],
        long_claimed=[
            {
                "ticket_type": "withdraw",
                "ticket_uid": "withdraw-1",
                "thread_id": 2,
                "region": "NA",
                "faction": "Alliance",
                "amount": 5_000,
            }
        ],
    )
    bot = _FakeBot()
    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]
    assert summary.released == 1
    assert summary.cancelled == 1


@pytest.mark.asyncio
async def test_tick_no_idle_or_long_claims_does_nothing() -> None:
    pool = _FakePool()
    bot = _FakeBot()
    summary = await tick(pool=pool, bot=bot)  # type: ignore[arg-type]
    assert summary.released == 0
    assert summary.cancelled == 0
    assert pool.calls == []
