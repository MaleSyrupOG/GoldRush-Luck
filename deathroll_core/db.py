"""asyncpg pool helpers for the DeathRoll platform.

Every bot acquires its own pool against the shared Postgres database with
its own DB role (deathroll_luck, deathroll_dw, etc.). The pool factory below
is intentionally minimal: it returns a pool from a DSN, with sane defaults
that match the load profile of a small Discord bot. Per-bot tuning lives
in the calling code.

Connection-level setup such as `SET app.audit_chain_key` is handled at
the database level (ALTER DATABASE deathroll SET app.audit_chain_key = ...)
so callers do not need to issue per-session SET statements; new
connections inherit the database setting automatically.
"""

from __future__ import annotations

from typing import Any, Protocol

import asyncpg


class Executor(Protocol):
    """Anything with the asyncpg query methods we use.

    Both ``asyncpg.Pool`` and ``asyncpg.Connection`` satisfy this Protocol,
    so the balance wrappers below accept either: callers can pass a Pool
    for one-shot operations or a Connection when bundling several into a
    single transaction.
    """

    async def fetchval(self, query: str, *args: Any, timeout: float | None = None) -> Any: ...
    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Any: ...
    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[Any]: ...
    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str: ...


async def create_pool(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    command_timeout: float = 30.0,
    statement_cache_size: int = 256,
) -> asyncpg.Pool:
    """Create an asyncpg connection pool with the DeathRoll defaults.

    The defaults aim for a single-bot deployment talking to a Postgres on
    the same Docker network: a small steady-state pool (1) with headroom
    for bursts (10), a 30-second statement timeout to surface stuck
    queries quickly, and a per-connection statement cache so repeated
    SECURITY DEFINER calls reuse their parsed plans.
    """
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        statement_cache_size=statement_cache_size,
    )
