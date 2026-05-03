"""Welcome dynamic embeds — reconciler for ``#how-to-deposit`` and ``#how-to-withdraw``.

Spec §5.7 step 5 (and Story 4.4) calls for the bot to post or edit
the two welcome embeds at startup, idempotent across restarts. The
content lives in ``dw.dynamic_embeds`` so admins can edit copy via
``/admin set-deposit-guide`` / ``/admin set-withdraw-guide`` without
a redeploy.

Behaviour:

1. For each managed embed (``how_to_deposit``, ``how_to_withdraw``):
   - Look up the row in ``dw.dynamic_embeds``.
   - If absent: resolve a channel id (passed by the caller, falling
     back to ``dw.global_config.value_int`` keyed by
     ``channel_id_<embed_key>``). If still absent, **skip** with
     ``reason='channel_id_unknown'`` — the operator hasn't run
     ``/admin setup`` yet; no crash, just a log line.
   - With a channel id, INSERT the row with the configured default
     title / description (so the embed has SOMETHING to render even
     before any admin edit).

2. Build the embed via ``how_to_deposit_dynamic_embed`` (the same
   builder ``/admin set-*-guide`` will use to render after an
   edit).

3. Reconcile the message:
   - ``message_id IS NULL`` → ``channel.send(embed=...)``, persist
     the new id.
   - ``message_id IS NOT NULL``:
     - Try ``channel.fetch_message(id)`` + ``message.edit(embed=...)``
       (idempotent path).
     - On ``discord.NotFound`` (admin deleted the message), repost
       and persist the new id. This is the self-healing branch.

The reconciler returns a typed ``ReconcileOutcome`` per managed
embed so callers can render a status embed and so tests can assert
the exact branch taken.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import discord
import structlog
from goldrush_core.db import Executor
from goldrush_core.embeds.dw_tickets import how_to_deposit_dynamic_embed

_log = structlog.get_logger(__name__)

# System actor id used for ``updated_by`` on auto-seeded rows.
_SYSTEM_ACTOR = 0


@dataclass(frozen=True)
class WelcomeDefault:
    """Static content seeded into ``dw.dynamic_embeds`` on first run."""

    embed_key: str
    title: str
    description: str


# Canonical seeds. Admins edit these via the ``/admin set-*-guide``
# command (Story 10.x); the seeds are only used the first time a
# row is INSERTed.
DEFAULT_WELCOMES: tuple[WelcomeDefault, ...] = (
    WelcomeDefault(
        embed_key="how_to_deposit",
        title="How to deposit",
        description=(
            "Run ``/deposit`` here, fill in your character, realm, region "
            "and faction, plus the gold amount you want to convert. The "
            "bot opens a private ticket where a cashier will claim your "
            "request. **The cashier never sends a trade request first — "
            "you approach them in-game.**"
        ),
    ),
    WelcomeDefault(
        embed_key="how_to_withdraw",
        title="How to withdraw",
        description=(
            "Run ``/withdraw`` here. The bot locks the requested amount "
            "on your balance and a cashier claims your ticket. After the "
            "in-game trade, ``/confirm`` finalises and the fee is taken "
            "from the gross amount. The amount you actually receive "
            "in-game is shown up front before you confirm."
        ),
    ),
    # Cashier-facing onboarding cheatsheet. Lives in the staff-only
    # ``#cashier-onboarding`` channel (provisioned by ``/admin-setup``
    # under the Cashier category). The text is structured as a numbered
    # walkthrough so a brand-new cashier can run their first ticket
    # end-to-end without anyone having to walk them through it.
    WelcomeDefault(
        embed_key="cashier_onboarding",
        title="Welcome — cashier onboarding",
        description=(
            "**1. Register your characters.** Run "
            "``/cashier-add-character`` for each WoW char + realm + "
            "region + faction you can trade from. The bot uses this "
            "list to match you with tickets your characters can "
            "actually fulfil.\n\n"
            "**2. Set yourself online.** Run ``/cashier-online`` "
            "before claiming. The roster in ``#online-cashiers`` "
            "updates within 30 s. Use ``/cashier-break`` for a short "
            "AFK and ``/cashier-offline`` when you're done — the bot "
            "auto-offlines anyone idle >1 h.\n\n"
            "**3. Claim a ticket.** When ``#cashier-alerts`` pings, "
            "open the linked ticket channel and run ``/claim``. You "
            "have 30 min of typing/clicking activity before the bot "
            "auto-releases the ticket back to the queue, and a 2 h "
            "hard cap on holding it.\n\n"
            "**4. Trade in-game.** "
            "**You NEVER send the trade request first — the user "
            "approaches you.** This is the anti-phishing rule the "
            "user-facing copy holds them to; it works only if you "
            "honour it. Confirm the user's character matches what "
            "they typed in the ticket before accepting any trade.\n\n"
            "**5. Run /confirm.** After the in-game trade settles, "
            "``/confirm`` in the ticket channel closes the ticket and "
            "credits / deducts the user's bot balance. ``/release`` "
            "hands the ticket back to the queue if you cannot "
            "complete it; ``/cancel`` is for when the user explicitly "
            "calls it off.\n\n"
            "**6. Disputes.** If something goes wrong (gold sent and "
            "the user denies it, partial trade, etc.), DO NOT cancel "
            "or confirm. Open a ticket in the support channel and "
            "ping ``@admin`` — they'll open a ``/admin-dispute-open`` "
            "and the case lands in ``#disputes`` for tracking."
        ),
    ),
)


ReconcileAction = Literal["posted", "edited", "reposted", "skipped"]


@dataclass(frozen=True)
class ReconcileOutcome:
    """Per-key result of one reconcile pass."""

    embed_key: str
    action: ReconcileAction
    message_id: int | None = None
    reason: str | None = None  # populated for ``action == 'skipped'``


# ---------------------------------------------------------------------------
# Single-key reconcile
# ---------------------------------------------------------------------------


async def reconcile_welcome_embed(
    *,
    pool: Executor,
    bot: discord.Client,
    embed_key: str,
    fallback_channel_id: int | None,
    default_title: str,
    default_description: str,
    actor_id: int = _SYSTEM_ACTOR,
) -> ReconcileOutcome:
    """Reconcile a single welcome embed.

    The orchestrator below calls this once per managed key. Pulled
    out as a separate function so it can be unit-tested without the
    multi-key plumbing.
    """
    row = await pool.fetchrow(
        "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
        embed_key,
    )

    # Resolve the target channel. The fallback (from
    # ``dw.global_config.channel_id_<embed_key>``) is the canonical
    # value — it's updated by ``/admin-setup`` so it reflects the
    # CURRENT channel structure. If the row's stored ``channel_id``
    # disagrees (for instance because the operator deleted the old
    # channels and re-ran ``/admin-setup``), we re-target the row to
    # the fallback and clear ``message_id`` so the next branch posts
    # a fresh message in the right channel.
    target_channel_id: int | None = None
    if fallback_channel_id is not None:
        target_channel_id = fallback_channel_id
    elif row is not None:
        target_channel_id = int(row["channel_id"])

    if target_channel_id is None:
        _log.info(
            "welcome_embed_skipped",
            embed_key=embed_key,
            reason="channel_id_unknown",
        )
        return ReconcileOutcome(
            embed_key=embed_key,
            action="skipped",
            reason="channel_id_unknown",
        )

    # If the row points at a stale channel id, repoint it. Clearing
    # message_id forces the next branch to ``channel.send`` rather
    # than ``message.edit`` (the old message either doesn't exist or
    # is unreachable from the new channel).
    if row is not None and int(row["channel_id"]) != target_channel_id:
        await pool.execute(
            """
            UPDATE dw.dynamic_embeds
                SET channel_id = $1, message_id = NULL, updated_at = NOW()
                WHERE embed_key = $2
            """,
            target_channel_id,
            embed_key,
        )
        row = await pool.fetchrow(
            "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
            embed_key,
        )
        _log.info(
            "welcome_embed_retargeted",
            embed_key=embed_key,
            new_channel_id=target_channel_id,
        )

    # Insert the row with default content if it didn't exist.
    if row is None:
        await pool.execute(
            """
            INSERT INTO dw.dynamic_embeds
                (embed_key, channel_id, title, description, updated_by)
            VALUES ($1, $2, $3, $4, $5)
            """,
            embed_key,
            target_channel_id,
            default_title,
            default_description,
            actor_id,
        )
        row = await pool.fetchrow(
            "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
            embed_key,
        )
        assert row is not None  # we just inserted it

    channel = bot.get_channel(target_channel_id)
    if channel is None:
        _log.warning(
            "welcome_embed_skipped",
            embed_key=embed_key,
            reason="channel_not_found",
            channel_id=target_channel_id,
        )
        return ReconcileOutcome(
            embed_key=embed_key,
            action="skipped",
            reason="channel_not_found",
        )

    embed = _build_embed_from_row(row)
    message_id = row["message_id"]

    if message_id is None:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, embed_key, sent.id)
        _log.info("welcome_embed_posted", embed_key=embed_key, message_id=sent.id)
        return ReconcileOutcome(
            embed_key=embed_key, action="posted", message_id=sent.id
        )

    # Try to edit; if the message is gone (admin deleted), repost.
    try:
        existing = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
        await existing.edit(embed=embed)
        _log.info(
            "welcome_embed_edited", embed_key=embed_key, message_id=int(message_id)
        )
        return ReconcileOutcome(
            embed_key=embed_key, action="edited", message_id=int(message_id)
        )
    except discord.NotFound:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, embed_key, sent.id)
        _log.info(
            "welcome_embed_reposted", embed_key=embed_key, message_id=sent.id
        )
        return ReconcileOutcome(
            embed_key=embed_key, action="reposted", message_id=sent.id
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def reconcile_welcome_embeds(
    *,
    pool: Executor,
    bot: discord.Client,
    welcomes: Sequence[WelcomeDefault] = DEFAULT_WELCOMES,
    actor_id: int = _SYSTEM_ACTOR,
) -> list[ReconcileOutcome]:
    """Run :func:`reconcile_welcome_embed` for every managed key.

    Channel ids are resolved per key from ``dw.global_config``
    (``channel_id_<embed_key>``). Story 10.x's ``/admin setup``
    populates those keys after the channel factory creates the
    Discord channels.
    """
    outcomes: list[ReconcileOutcome] = []
    for default in welcomes:
        fallback = await _get_config_channel(pool, default.embed_key)
        outcome = await reconcile_welcome_embed(
            pool=pool,
            bot=bot,
            embed_key=default.embed_key,
            fallback_channel_id=fallback,
            default_title=default.title,
            default_description=default.description,
            actor_id=actor_id,
        )
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_config_channel(pool: Executor, embed_key: str) -> int | None:
    """Read ``channel_id_<embed_key>`` from ``dw.global_config``."""
    row = await pool.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = $1",
        f"channel_id_{embed_key}",
    )
    if row is None or row["value_int"] is None:
        return None
    return int(row["value_int"])


async def _persist_message_id(pool: Executor, embed_key: str, message_id: int) -> None:
    """UPDATE ``dw.dynamic_embeds.message_id`` for the given embed_key."""
    await pool.execute(
        """
        UPDATE dw.dynamic_embeds
            SET message_id = $1, updated_at = NOW()
            WHERE embed_key = $2
        """,
        message_id,
        embed_key,
    )


async def update_dynamic_embed_content(
    *,
    pool: Executor,
    bot: discord.Client,
    embed_key: str,
    title: str,
    description: str,
    actor_id: int,
) -> ReconcileOutcome:
    """Apply a Story 10.3 admin edit to a dynamic embed row + live message.

    Single source of truth for the ``/admin-set-deposit-guide`` and
    ``/admin-set-withdraw-guide`` flow:

    1. UPDATE ``dw.dynamic_embeds`` with the new title + description
       (other columns — color_hex, fields, image, footer — are left
       intact; v1.x can extend the modal to surface them).
    2. Re-render the embed via the same builder the welcome reconciler
       uses, so the visual contract stays consistent.
    3. Edit the live Discord message in place. On ``discord.NotFound``
       (admin manually deleted), repost and persist the new id.

    Returns the same ``ReconcileOutcome`` shape the welcome reconciler
    uses so the cog renders a uniform "edited" / "reposted" / "skipped"
    summary.
    """
    await pool.execute(
        """
        UPDATE dw.dynamic_embeds
            SET title       = $1,
                description = $2,
                updated_at  = NOW(),
                updated_by  = $3
            WHERE embed_key = $4
        """,
        title,
        description,
        actor_id,
        embed_key,
    )

    row = await pool.fetchrow(
        "SELECT * FROM dw.dynamic_embeds WHERE embed_key = $1",
        embed_key,
    )
    if row is None:
        # The row didn't exist — first edit before /admin-setup ran.
        # Defer to the reconciler which knows how to seed.
        _log.warning("dynamic_embed_edit_no_row", embed_key=embed_key)
        return ReconcileOutcome(
            embed_key=embed_key,
            action="skipped",
            reason="no_row_yet",
        )

    channel_id = row["channel_id"]
    if channel_id is None:
        return ReconcileOutcome(
            embed_key=embed_key,
            action="skipped",
            reason="channel_id_unknown",
        )
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return ReconcileOutcome(
            embed_key=embed_key,
            action="skipped",
            reason="channel_not_found",
        )

    embed = _build_embed_from_row(row)
    message_id = row["message_id"]
    if message_id is None:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, embed_key, sent.id)
        return ReconcileOutcome(
            embed_key=embed_key, action="posted", message_id=sent.id
        )
    try:
        existing = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
        await existing.edit(embed=embed)
        return ReconcileOutcome(
            embed_key=embed_key, action="edited", message_id=int(message_id)
        )
    except discord.NotFound:
        sent = await channel.send(embed=embed)  # type: ignore[union-attr]
        await _persist_message_id(pool, embed_key, sent.id)
        return ReconcileOutcome(
            embed_key=embed_key, action="reposted", message_id=sent.id
        )


def _build_embed_from_row(row: Any) -> discord.Embed:
    """Render an embed from a ``dw.dynamic_embeds`` row.

    The ``fields`` column is JSONB; asyncpg returns it as a Python
    list-of-dicts, but our embed builder expects a JSON string (the
    same shape ``EditDynamicEmbedInput`` validates). We re-serialise
    on the way out so the builder's malformed-fallback path is the
    only thing it has to handle.
    """
    fields_json = json.dumps(row["fields"]) if row["fields"] else None
    return how_to_deposit_dynamic_embed(
        title=row["title"],
        description=row["description"],
        color_hex=row["color_hex"],
        fields_json=fields_json,
        image_url=row["image_url"],
        footer_text=row["footer_text"],
    )


__all__ = [
    "DEFAULT_WELCOMES",
    "ReconcileAction",
    "ReconcileOutcome",
    "WelcomeDefault",
    "reconcile_welcome_embed",
    "reconcile_welcome_embeds",
    "update_dynamic_embed_content",
]
