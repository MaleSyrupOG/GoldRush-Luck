"""Background task that keeps the ``#online-cashiers`` embed fresh.

Story 4.5 wires this up. Behaviour mirrors the welcome reconciler
in ``deathroll_deposit_withdraw.welcome`` but with two differences:

1. The embed content is computed from a live ``RosterSnapshot``
   (read every 30 s) rather than from a row's ``title`` /
   ``description``. The ``dw.dynamic_embeds`` row exists only as
   a stable place to persist the ``message_id`` so restarts don't
   create duplicate live messages.

2. The reconcile is repeated on a fixed interval, not just at
   startup. ``OnlineCashiersUpdater`` wraps ``tick`` in a
   cancellable asyncio loop so the bot can shut down cleanly
   (e.g., on SIGTERM in the container).

Tests in ``tests/unit/dw/test_live_updater.py`` exercise ``tick``
directly with in-process fakes; ``OnlineCashiersUpdater`` is
exercised at the start / stop semantics layer (no real interval
sleeps).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
import structlog
from deathroll_core.balance.cashier_roster import fetch_online_roster
from deathroll_core.db import Executor
from deathroll_core.embeds.dw_tickets import online_cashiers_live_embed

if TYPE_CHECKING:
    from deathroll_core.balance.cashier_roster import RosterSnapshot


_log = structlog.get_logger(__name__)
_EMBED_KEY = "online_cashiers"


# ---------------------------------------------------------------------------
# tick — single iteration
# ---------------------------------------------------------------------------


async def tick(
    *,
    pool: Executor,
    bot: discord.Client,
    channel_id: int,
) -> int | None:
    """Run one update cycle for the online-cashiers live embed.

    Returns the message id of the live message after the cycle, or
    ``None`` if the cycle skipped (channel not resolvable). On a
    real run this happens every 30 s from
    :class:`OnlineCashiersUpdater`.

    Branches:

    1. Resolve the ``dw.dynamic_embeds`` row keyed ``online_cashiers``;
       INSERT it if missing so the persistence layer is consistent
       with the welcome embeds.
    2. Build the embed from a fresh ``RosterSnapshot``.
    3. If ``message_id IS NULL`` → ``channel.send`` and persist the
       new id.
    4. Otherwise edit in place; on ``discord.NotFound``, repost.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        _log.warning("online_cashiers_skipped", reason="channel_not_found", channel_id=channel_id)
        return None

    snapshot: RosterSnapshot = await fetch_online_roster(pool)
    embed = online_cashiers_live_embed(snapshot=snapshot, last_updated=datetime.now(UTC))

    row = await pool.fetchrow(
        "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
        _EMBED_KEY,
    )

    # Insert a placeholder row if it doesn't exist yet — title /
    # description are unused (the embed is computed live) but the
    # NOT NULL constraints require something.
    if row is None:
        await pool.execute(
            """
            INSERT INTO dw.dynamic_embeds
                (embed_key, channel_id, title, description, updated_by)
            VALUES ($1, $2, $3, $4, $5)
            """,
            _EMBED_KEY,
            channel_id,
            "Online cashiers",
            "Live roster updated every 30 seconds.",
            0,  # system actor
        )
        row = await pool.fetchrow(
            "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
            _EMBED_KEY,
        )
        assert row is not None

    message_id = row["message_id"]

    if message_id is None:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, sent.id)
        _log.info("online_cashiers_posted", message_id=sent.id)
        return int(sent.id)

    try:
        existing = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
        await existing.edit(embed=embed)
        return int(message_id)
    except discord.NotFound:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, sent.id)
        _log.info("online_cashiers_reposted", message_id=sent.id)
        return int(sent.id)


async def _persist_message_id(pool: Executor, message_id: int) -> None:
    await pool.execute(
        """
        UPDATE dw.dynamic_embeds
            SET message_id = $1, updated_at = NOW()
            WHERE embed_key = $2
        """,
        message_id,
        _EMBED_KEY,
    )


# ---------------------------------------------------------------------------
# OnlineCashiersUpdater — cancellable loop
# ---------------------------------------------------------------------------


class OnlineCashiersUpdater:
    """Manages the periodic ``tick`` invocation.

    Holds a single ``asyncio.Task`` so the bot can ``stop()`` cleanly
    on shutdown. Errors during ``tick`` are caught and logged so a
    transient DB blip doesn't kill the loop — the next iteration
    retries.
    """

    def __init__(
        self,
        *,
        pool: Executor,
        bot: discord.Client,
        channel_id: int,
        interval: float = 30.0,
    ) -> None:
        self._pool = pool
        self._bot = bot
        self._channel_id = channel_id
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Spawn the loop. Idempotent — calling twice is a no-op."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="online-cashiers-updater")

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to exit cleanly."""
        if self._task is None:
            return
        self._stop_event.set()
        # Cancel as a backup in case the task is sleeping.
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception) as e:
            _log.debug("online_cashiers_updater_stop_swallow", error=str(e))
        self._task = None

    async def _run(self) -> None:
        # Run an initial tick immediately so the embed appears on
        # first start without waiting ``interval`` seconds.
        try:
            await tick(pool=self._pool, bot=self._bot, channel_id=self._channel_id)
        except Exception as e:
            _log.exception("online_cashiers_tick_failed", error=str(e))

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval
                )
                # If wait returned without timeout, stop was requested.
                break
            except TimeoutError:
                pass

            try:
                await tick(pool=self._pool, bot=self._bot, channel_id=self._channel_id)
            except Exception as e:
                _log.exception("online_cashiers_tick_failed", error=str(e))


__all__ = ["OnlineCashiersUpdater", "tick"]
