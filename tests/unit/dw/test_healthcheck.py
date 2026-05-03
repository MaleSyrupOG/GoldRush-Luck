"""Unit tests for `deathroll_deposit_withdraw.healthcheck`.

The healthcheck script is invoked by the Docker HEALTHCHECK directive.
It must exit 0 if the bot can read from Postgres, 1 otherwise. The
DB call surface is small (``SELECT 1``) so the tests use in-process
fakes — no real Postgres required.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from deathroll_deposit_withdraw.healthcheck import ping


class _FakeOk:
    """Returns the integer 1 from any fetchval, like a real ``SELECT 1``."""

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> int:
        return 1


class _FakeWrong:
    """Returns the wrong value — should fail the ping."""

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> int:
        return 7


class _FakeRaises:
    """Raises like a Postgres connection error."""

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> int:
        raise RuntimeError("connection refused")


class _FakeSlow:
    """Sleeps longer than any reasonable ping timeout — must trip the wait_for."""

    async def fetchval(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> int:
        await asyncio.sleep(10)
        return 1  # never reached in test


@pytest.mark.asyncio
async def test_ping_returns_true_on_select_one() -> None:
    assert await ping(_FakeOk()) is True


@pytest.mark.asyncio
async def test_ping_returns_false_on_unexpected_value() -> None:
    """A live DB returning ``7`` for ``SELECT 1`` means something is very wrong;
    the healthcheck must not paper over that with a green light."""
    assert await ping(_FakeWrong()) is False


@pytest.mark.asyncio
async def test_ping_returns_false_on_exception() -> None:
    assert await ping(_FakeRaises()) is False


@pytest.mark.asyncio
async def test_ping_returns_false_on_timeout() -> None:
    """Force a tight timeout so the slow fake trips ``asyncio.wait_for``."""
    assert await ping(_FakeSlow(), timeout=0.05) is False


def test_main_returns_1_when_postgres_dsn_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the env var is missing, the healthcheck must fail loudly and return 1.

    A missing DSN is an operational error (env file not mounted, typo
    in compose) — green-lighting it would mask a misconfiguration."""
    from deathroll_deposit_withdraw import healthcheck as hc

    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    assert hc.main() == 1


def test_main_returns_0_when_pool_factory_pings_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A well-behaved pool returns 1 from SELECT 1; main exits 0."""
    from deathroll_deposit_withdraw import healthcheck as hc

    monkeypatch.setenv("POSTGRES_DSN", "postgresql://x@y/z")

    class _FakePool:
        async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> int:
            return 1

        async def close(self) -> None:
            pass

    async def _factory(**kwargs: Any) -> _FakePool:
        return _FakePool()

    assert hc.main(pool_factory=_factory) == 0


def test_main_returns_1_when_pool_factory_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the pool cannot be opened (Postgres down, wrong DSN), exit 1."""
    from deathroll_deposit_withdraw import healthcheck as hc

    monkeypatch.setenv("POSTGRES_DSN", "postgresql://x@y/z")

    async def _factory(**kwargs: Any) -> Any:
        raise OSError("connection refused")

    assert hc.main(pool_factory=_factory) == 1
