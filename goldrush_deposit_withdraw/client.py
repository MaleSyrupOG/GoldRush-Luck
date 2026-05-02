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
from goldrush_core.ratelimit import FixedWindowLimiter

from goldrush_deposit_withdraw.cashiers.live_updater import OnlineCashiersUpdater
from goldrush_deposit_withdraw.welcome import reconcile_welcome_embeds
from goldrush_deposit_withdraw.workers.ticket_timeout import TicketTimeoutWorker

# Cog import paths. The six canonical cogs map one-for-one to the
# command families in spec §5.1 (account, admin, cashier, deposit,
# ticket, withdraw). ``setup_hook`` iterates this list and calls
# ``self.load_extension(ext)`` for each entry. Slash commands inside
# each cog are registered against ``bot.tree`` and synced per-guild
# from ``on_ready``.
EXTENSIONS: tuple[str, ...] = (
    "goldrush_deposit_withdraw.cogs.account",
    "goldrush_deposit_withdraw.cogs.admin",
    "goldrush_deposit_withdraw.cogs.cashier",
    "goldrush_deposit_withdraw.cogs.deposit",
    "goldrush_deposit_withdraw.cogs.ticket",
    "goldrush_deposit_withdraw.cogs.withdraw",
)


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
    rate_limiters: dict[str, FixedWindowLimiter]
    _pool_factory: PoolFactory
    _log: structlog.types.FilteringBoundLogger
    _online_cashiers_updater: OnlineCashiersUpdater | None
    _ticket_timeout_worker: TicketTimeoutWorker | None

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
        self._online_cashiers_updater = None
        self._ticket_timeout_worker = None
        # Rate limiters keyed by command family. Spec: 1 ticket
        # creation per user per 60 s for both /deposit and /withdraw.
        # Other limiters (cashier set-status, /help) can be added by
        # cogs at load time without re-architecting.
        self.rate_limiters = {
            "deposit_create": FixedWindowLimiter(capacity=1, window_seconds=60.0),
            "withdraw_create": FixedWindowLimiter(capacity=1, window_seconds=60.0),
        }

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

    async def on_ready(self) -> None:
        """Fired by discord.py once the gateway is ready.

        Spec §5.7:
        - step 3: sync the command tree to the configured guild only
          (per-guild sync is instant; global sync takes up to an hour).
        - step 5: ensure the welcome dynamic embeds are posted /
          edited (idempotent reconcile — see ``welcome.py``).

        ``on_ready`` may fire multiple times across one process if
        Discord reconnects; both steps are idempotent so re-runs are
        safe (a re-run just re-syncs the tree and edits embeds in
        place).

        Note on the sync: cog ``@app_commands.command`` decorators
        register globally by default; ``copy_global_to(guild)`` is
        the canonical discord.py 2.x recipe to mirror them into the
        per-guild scope before the ``sync(guild=...)`` call so
        commands actually appear in the test server immediately
        rather than after Discord's ~1h global propagation.
        """
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        user_id = self.user.id if self.user is not None else 0
        self._log.info(
            "ready",
            user_id=user_id,
            guild_id=self.settings.guild_id,
            command_count=len(synced),
        )

        # Reconcile the dynamic welcome embeds. Wrap in a broad except
        # so a transient DB hiccup or missing channel doesn't stop the
        # bot from being interactive — the reconcile retries on the
        # next ready event.
        if self.pool is not None:
            try:
                outcomes = await reconcile_welcome_embeds(pool=self.pool, bot=self)
                self._log.info(
                    "welcome_embeds_reconciled",
                    outcomes={o.embed_key: o.action for o in outcomes},
                )
            except Exception as e:
                self._log.exception("welcome_embeds_failed", error=str(e))

            # Spec §5.7 step 6: start the online-cashiers updater. Like
            # the welcome reconcile this is wrapped — a missing
            # channel-id or DB blip must not stop the bot from being
            # interactive. Idempotent across reconnects: ``start()``
            # is a no-op if the loop is already running.
            try:
                channel_id = await _resolve_online_cashiers_channel(self.pool)
                if channel_id is not None and self._online_cashiers_updater is None:
                    self._online_cashiers_updater = OnlineCashiersUpdater(
                        pool=self.pool,
                        bot=self,
                        channel_id=channel_id,
                    )
                    self._online_cashiers_updater.start()
                    self._log.info(
                        "online_cashiers_updater_started",
                        channel_id=channel_id,
                    )
            except Exception as e:
                self._log.exception("online_cashiers_updater_failed", error=str(e))

            # Story 8.1: spin up the ticket-timeout worker. Idempotent
            # across reconnects — ``start()`` is a no-op if the loop
            # is already running.
            try:
                if self._ticket_timeout_worker is None:
                    self._ticket_timeout_worker = TicketTimeoutWorker(
                        pool=self.pool,
                        bot=self,
                    )
                    self._ticket_timeout_worker.start()
                    self._log.info("ticket_timeout_worker_started")
            except Exception as e:
                self._log.exception("ticket_timeout_worker_failed", error=str(e))

    async def close_pool(self) -> None:
        """Close the DB pool and stop background tasks — used on shutdown.

        ``commands.Bot.close()`` shuts down the gateway connection
        but does NOT touch our pool or our background tasks; we
        own those, so we tear them down here.
        """
        if self._online_cashiers_updater is not None:
            await self._online_cashiers_updater.stop()
            self._online_cashiers_updater = None
        if self._ticket_timeout_worker is not None:
            await self._ticket_timeout_worker.stop()
            self._ticket_timeout_worker = None
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


async def _resolve_online_cashiers_channel(pool: asyncpg.Pool) -> int | None:
    """Read ``channel_id_online_cashiers`` from ``dw.global_config``.

    Returns ``None`` when the operator hasn't yet run ``/admin
    setup`` (Story 10.x); the updater simply isn't started until
    a subsequent on_ready picks up the persisted id.
    """
    row = await pool.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = $1",
        "channel_id_online_cashiers",
    )
    if row is None or row["value_int"] is None:
        return None
    return int(row["value_int"])


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
