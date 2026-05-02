"""Cashier alert poster (Story 5.3).

After a ticket is opened, the bot posts a ``cashier_alert_embed``
in ``#cashier-alerts`` (or whatever channel the operator bound to
``channel_id_cashier_alerts`` in ``dw.global_config``). The embed
calls out the ticket meta and the list of cashiers currently
online whose active chars cover the (region, faction) — the in-
channel ``@cashier`` mention surfaces it across reasonable client
state.

The post is best-effort: if the channel id is not yet configured
(operator hasn't run ``/admin setup``) or the channel can no
longer be resolved, the function quietly skips. The in-thread
``@cashier`` ping in the ticket channel remains as a fallback so
no ticket goes unannounced.
"""

from __future__ import annotations

from typing import Literal

import discord
import structlog
from goldrush_core.balance.cashier_matcher import find_compatible_cashiers
from goldrush_core.balance.cashier_roster import fetch_online_roster
from goldrush_core.db import Executor
from goldrush_core.discord_helpers.channel_binding import resolve_channel_id
from goldrush_core.discord_helpers.role_binding import role_mention
from goldrush_core.embeds.dw_tickets import cashier_alert_embed

_log = structlog.get_logger(__name__)


async def post_cashier_alert(
    *,
    pool: Executor,
    bot: discord.Client,
    ticket_uid: str,
    ticket_type: Literal["deposit", "withdraw"],
    region: Literal["EU", "NA"],
    faction: Literal["Alliance", "Horde"],
    amount: int,
    ticket_channel_mention: str,
) -> None:
    """Post the cashier alert. Best-effort — never raises to the caller.

    ``ticket_channel_mention`` is the ``<#channel_id>`` of the
    private ticket thread/channel; cashiers click it to jump
    straight to the ticket and run ``/claim``.
    """
    channel_id = await resolve_channel_id(pool, "cashier_alerts")
    if channel_id is None:
        _log.info(
            "cashier_alert_skipped",
            reason="channel_id_unknown",
            ticket_uid=ticket_uid,
        )
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        _log.warning(
            "cashier_alert_skipped",
            reason="channel_not_found",
            channel_id=channel_id,
            ticket_uid=ticket_uid,
        )
        return

    snapshot = await fetch_online_roster(pool)
    compatible = find_compatible_cashiers(
        snapshot, region=region, faction=faction
    )
    mentions = tuple(f"<@{e.discord_id}>" for e in compatible)

    embed = cashier_alert_embed(
        ticket_uid=ticket_uid,
        ticket_type=ticket_type,
        region=region,
        faction=faction,
        amount=amount,
        channel_mention=ticket_channel_mention,
        compatible_cashiers=mentions,
    )

    cashier_ping = await role_mention(pool, "cashier")
    try:
        await channel.send(  # type: ignore[union-attr]
            content=cashier_ping,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        _log.info(
            "cashier_alert_posted",
            ticket_uid=ticket_uid,
            ticket_type=ticket_type,
            compatible_count=len(mentions),
        )
    except Exception as e:
        _log.exception("cashier_alert_failed", ticket_uid=ticket_uid, error=str(e))


__all__ = ["post_cashier_alert"]
