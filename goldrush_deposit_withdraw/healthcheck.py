"""Healthcheck script for the Docker HEALTHCHECK directive.

Run with: ``python -m goldrush_deposit_withdraw.healthcheck``

Behaviour:

1. Read ``POSTGRES_DSN`` from the environment (set by Compose).
2. Open a tiny asyncpg pool (1 conn) with a short command timeout.
3. Run ``SELECT 1`` and verify the result is exactly ``1``.
4. Exit ``0`` on success, ``1`` on any failure.

The reason ``ping`` is split out as a top-level coroutine taking an
``Executor`` (rather than collapsed into ``main``) is testability:
unit tests inject a fake ``fetchval`` and exercise every failure
branch without spinning up Postgres.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from goldrush_core.db import Executor


async def ping(executor: Executor, *, timeout: float = 3.0) -> bool:
    """Return ``True`` if ``SELECT 1`` returns exactly 1 within ``timeout``.

    A truthy result on a connection-class object is enough to prove
    the pool can borrow a connection and that Postgres is responsive
    on the configured DSN. A wrong return value, an exception, or a
    timeout each return ``False``; the caller maps that to exit code
    ``1`` so the orchestrator (Docker) marks the container unhealthy.
    """
    try:
        result = await asyncio.wait_for(
            executor.fetchval("SELECT 1"), timeout=timeout
        )
    except (TimeoutError, Exception):
        return False
    # ``fetchval`` is typed ``Any`` (the asyncpg Protocol cannot narrow
    # the column type); ``bool(...)`` collapses the comparison result
    # back into a strict bool for the type checker.
    return bool(result == 1)


# Pool factory injection point. ``main`` defaults to ``asyncpg.create_pool``
# but accepts a fake from tests so the failure paths can be exercised
# without a real Postgres.
PoolFactory = Callable[..., Awaitable[Any]]


def main(
    *,
    pool_factory: PoolFactory | None = None,
    dsn: str | None = None,
) -> int:
    """Bin entry — return 0 on healthy DB, 1 otherwise.

    Both ``dsn`` and ``pool_factory`` exist for test injection; in
    production they default to ``$POSTGRES_DSN`` and
    ``asyncpg.create_pool``.
    """
    if dsn is None:
        dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        sys.stderr.write("healthcheck: POSTGRES_DSN not set\n")
        return 1

    factory: PoolFactory = pool_factory if pool_factory is not None else asyncpg.create_pool
    return asyncio.run(_run(dsn, factory))


async def _run(dsn: str, pool_factory: PoolFactory) -> int:
    """Open a small pool, ping it, close cleanly."""
    try:
        pool = await pool_factory(
            dsn=dsn,
            min_size=1,
            max_size=1,
            command_timeout=3.0,
        )
    except Exception as e:
        sys.stderr.write(f"healthcheck: failed to open pool: {e}\n")
        return 1

    try:
        ok = await ping(pool)
    finally:
        # Close even if ping raised; we own the pool's lifecycle here.
        # Errors during close are non-fatal at this exit point — the
        # process is going to terminate anyway and the OS will reap
        # the sockets.
        try:
            await pool.close()
        except Exception as e:
            sys.stderr.write(f"healthcheck: pool.close() raised: {e}\n")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
