"""``#disputes`` channel poster + message-id roundtrip (Story 9.2).

Distinct from ``audit_log.py`` (which posts a transient note in
``#audit-log`` for every economic event): the disputes module owns
the long-lived embed that lives in ``#disputes`` for one specific
dispute. The lifecycle is:

    open    → post fresh embed, persist ``discord_message_id``
    resolve → fetch the same message and EDIT it in place
    reject  → fetch the same message and EDIT it in place

Editing the original message keeps the channel transcript readable:
admins scrolling ``#disputes`` see one row per dispute (not three —
open + resolve + reject would double-bookkeeping).

Best-effort throughout. Discord API failures are logged and
swallowed — failing to post should never roll back the SQL state
change that triggered the post (the SECURITY DEFINER fn is the
forensic source of truth). Story 9.2 AC: "Message IDs persisted on
the dw.disputes row".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import discord
import structlog
from deathroll_core.db import Executor
from deathroll_core.discord_helpers.channel_binding import resolve_channel_id
from deathroll_core.embeds.dw_tickets import dispute_open_embed

_log = structlog.get_logger(__name__)


TicketType = Literal["deposit", "withdraw"]
OpenerRole = Literal["admin", "user", "system"]


async def post_dispute_open_embed(
    *,
    pool: Executor,
    bot: discord.Client,
    dispute_id: int,
    ticket_type: TicketType,
    ticket_uid: str,
    opener_mention: str,
    opener_role: OpenerRole,
    reason: str,
    opened_at: datetime,
) -> None:
    """Post the dispute_open_embed in ``#disputes`` and persist the message id.

    Skips silently when:
    - ``#disputes`` is not configured (operator hasn't run ``/admin-setup``),
    - the channel id no longer resolves to a real channel,
    - sending fails for any Discord-side reason.

    Persists ``dw.disputes.discord_message_id`` on success so the
    resolve/reject paths can edit the same message instead of posting a
    new one.
    """
    channel_id = await resolve_channel_id(pool, "disputes")
    if channel_id is None:
        _log.info("dispute_post_skipped", reason="channel_id_unknown", dispute_id=dispute_id)
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        _log.warning(
            "dispute_post_skipped",
            reason="channel_not_found",
            dispute_id=dispute_id,
            channel_id=channel_id,
        )
        return

    embed = dispute_open_embed(
        dispute_id=dispute_id,
        ticket_uid=ticket_uid,
        ticket_type=ticket_type,
        opener_mention=opener_mention,
        opener_role=opener_role,
        reason=reason,
        opened_at=opened_at,
    )
    try:
        msg = await channel.send(embed=embed)  # type: ignore[union-attr]
    except Exception as e:
        _log.exception(
            "dispute_post_failed",
            dispute_id=dispute_id,
            error=str(e),
        )
        return

    try:
        await pool.execute(
            "UPDATE dw.disputes SET discord_message_id = $1 WHERE id = $2",
            msg.id,
            dispute_id,
        )
        _log.info(
            "dispute_posted",
            dispute_id=dispute_id,
            message_id=msg.id,
        )
    except Exception as e:
        # The post made it but persistence failed — surface in logs so
        # the gap is visible during forensics. The dispute is still
        # functional; only the resolve/reject embed-edit will degrade
        # to a no-op (the row's discord_message_id stays NULL and the
        # update_dispute_status_embed helper short-circuits).
        _log.exception(
            "dispute_message_id_persist_failed",
            dispute_id=dispute_id,
            message_id=msg.id,
            error=str(e),
        )


async def update_dispute_status_embed(
    *,
    pool: Executor,
    bot: discord.Client,
    dispute_id: int,
    new_embed: discord.Embed,
) -> None:
    """Edit the dispute's existing ``#disputes`` message with ``new_embed``.

    No-op when:
    - the dispute row has no ``discord_message_id`` (open post failed
      or the row predates Story 9.2's column),
    - the channel can't be resolved (admin deleted ``#disputes``),
    - the message itself was deleted on the Discord side.

    All paths swallow exceptions and log; never raises back to the
    caller. The audit-log channel poster covers the redundancy: even
    if the ``#disputes`` edit silently no-ops, the resolve/reject
    event still surfaces in ``#audit-log``.
    """
    row = await pool.fetchrow(
        "SELECT discord_message_id FROM dw.disputes WHERE id = $1",
        dispute_id,
    )
    if row is None or row["discord_message_id"] is None:
        _log.info(
            "dispute_status_edit_skipped",
            reason="no_message_id",
            dispute_id=dispute_id,
        )
        return

    channel_id = await resolve_channel_id(pool, "disputes")
    if channel_id is None:
        _log.info(
            "dispute_status_edit_skipped",
            reason="channel_id_unknown",
            dispute_id=dispute_id,
        )
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        _log.warning(
            "dispute_status_edit_skipped",
            reason="channel_not_found",
            dispute_id=dispute_id,
            channel_id=channel_id,
        )
        return

    message_id = int(row["discord_message_id"])
    try:
        msg = await channel.fetch_message(message_id)  # type: ignore[union-attr]
    except discord.NotFound:
        _log.info(
            "dispute_status_edit_skipped",
            reason="message_deleted",
            dispute_id=dispute_id,
            message_id=message_id,
        )
        return
    except Exception as e:
        _log.exception(
            "dispute_status_edit_failed",
            dispute_id=dispute_id,
            stage="fetch",
            error=str(e),
        )
        return

    try:
        await msg.edit(embed=new_embed)
        _log.info(
            "dispute_status_embed_edited",
            dispute_id=dispute_id,
            message_id=message_id,
        )
    except Exception as e:
        _log.exception(
            "dispute_status_edit_failed",
            dispute_id=dispute_id,
            stage="edit",
            error=str(e),
        )


__all__ = [
    "post_dispute_open_embed",
    "update_dispute_status_embed",
]
