"""Discord client subclass for the GoldRush Deposit/Withdraw bot.

``DwBot`` is a thin extension of ``discord.ext.commands.Bot``:

- Holds a strongly-typed ``DwSettings`` and an asyncpg ``Pool``.
- ``setup_hook`` (called by discord.py before ``on_ready``) opens
  the pool with the ``goldrush_dw`` Postgres role.
- ``EXTENSIONS`` is the canonical list of cogs to load. It is
  intentionally empty in Story 4.1; Story 4.2 populates it and
  ``setup_hook`` learns to ``load_extension`` them.

The pool factory is injectable so tests can pass a fake. In
production callers either omit it (default ``asyncpg.create_pool``)
or pass a pre-built pool factory if they need custom tuning.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import discord
import structlog
from discord.ext import commands
from goldrush_core.config import DwSettings
from goldrush_core.db import create_pool

# Cog import paths. Empty in Story 4.1; populated in Story 4.2 with
# the six canonical cogs (deposit, withdraw, ticket, cashier, admin,
# account). ``setup_hook`` iterates this list and calls
# ``self.load_extension(ext)`` for each entry.
EXTENSIONS: tuple[str, ...] = ()


# Type alias for the asyncpg pool factory. Default is the real
# ``asyncpg.create_pool``; tests pass an in-process fake.
PoolFactory = Callable[..., Awaitable[asyncpg.Pool]]


class DwBot(commands.Bot):
    """Bot subclass that owns a DB pool plus the canonical settings.

    Construction is via ``build_bot(settings)`` rather than direct
    instantiation so the intents and command prefix stay consistent
    across entry points (``__main__`` and tests).
    """

    settings: DwSettings
    pool: asyncpg.Pool | None
    _pool_factory: PoolFactory
    _log: structlog.types.FilteringBoundLogger

    def __init__(
        self,
        *,
        settings: DwSettings,
        pool_factory: PoolFactory,
        intents: discord.Intents,
        **kwargs: Any,
    ) -> None:
        # Slash-only bot — no message-prefix commands. We still pass a
        # prefix because ``commands.Bot`` requires one; the value is
        # never used because every command is a slash command.
        super().__init__(command_prefix="!unused", intents=intents, **kwargs)
        self.settings = settings
        self._pool_factory = pool_factory
        self.pool = None
        self._log = structlog.get_logger(__name__)

    async def setup_hook(self) -> None:
        """Open the DB pool and prepare for Discord login.

        discord.py invokes this once per process, after ``__init__``
        and before the gateway connection is established. It is the
        canonical place to run any async initialisation that needs to
        be ready by the time ``on_ready`` fires.

        Cog loading (Story 4.2) and the welcome-embed reconciler
        (Story 4.4) hook in here too.
        """
        dsn = self.settings.postgres_dsn.get_secret_value()
        self.pool = await self._pool_factory(dsn=dsn)
        self._log.info(
            "db_pool_ready",
            dsn_host=_redact_dsn(dsn),
        )

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            self._log.info("cog_loaded", extension=ext)

    async def close_pool(self) -> None:
        """Close the DB pool — used on shutdown and by tests.

        ``commands.Bot.close()`` shuts down the gateway connection
        but does NOT touch our pool, so we expose this hook for
        cleanup. A clean shutdown invokes both.
        """
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


def _redact_dsn(dsn: str) -> str:
    """Strip the password from a DSN for log output.

    ``postgresql://user:password@host:port/db`` → ``host:port/db``.
    Defensive: even with SecretStr we never log the raw DSN; this
    helper keeps the host/db visible for diagnostics without leaking
    the password.
    """
    if "@" in dsn:
        return dsn.rsplit("@", 1)[1]
    return dsn


def build_bot(
    settings: DwSettings,
    *,
    pool_factory: PoolFactory | None = None,
) -> DwBot:
    """Construct a ``DwBot`` with the canonical intent set.

    Spec §6.6 forbids privileged intents in v1 — ``Intents.default()``
    already excludes them. Future opt-ins (e.g., presence) need an
    explicit decision and a Discord-side privileged-intent toggle.
    """
    factory = pool_factory if pool_factory is not None else create_pool
    intents = discord.Intents.default()
    return DwBot(
        settings=settings,
        pool_factory=factory,
        intents=intents,
    )
