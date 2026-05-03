"""Audit-log Discord channel poster.

Distinct from ``core.audit_log`` (the immutable hash-chained DB
table that records every economic event with cryptographic
integrity): this module posts a HUMAN-VISIBLE summary of those
events into the ``#audit-log`` Discord channel that
``/admin-setup`` provisions under the ``Admin`` category.

Admins use the channel for at-a-glance oversight: who confirmed
which ticket, when a force-cancel happened, when a cashier was
forced offline. Only the bot writes; only @admin reads.

The DB audit log remains the source of truth for forensics —
this Discord surface is convenience tooling and best-effort:
posting failures are logged and swallowed so a failed Discord
post never rolls back an economic action.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import discord
import structlog
from goldrush_core.db import Executor
from goldrush_core.discord_helpers.channel_binding import resolve_channel_id

_log = structlog.get_logger(__name__)


_ACTION_COLOR: dict[str, int] = {
    # Per the design system (Luck §6.3 / D/W §5.6).
    "ticket_opened": 0x5B7CC9,        # HOUSE blue
    "ticket_claimed": 0xF2B22A,       # GOLD
    "ticket_released": 0xC8511C,      # EMBER
    "ticket_confirmed": 0x5DBE5A,     # WIN green
    "ticket_cancelled": 0xD8231A,     # BUST red
    "force_cancel": 0xD8231A,
    "force_cashier_offline": 0xC8511C,
    "force_close_thread": 0xC8511C,
    "treasury": 0xF2B22A,
    # Story 9.1 — disputes
    "dispute_opened": 0xD8231A,       # BUST red
    "dispute_resolved": 0x5DBE5A,     # WIN green
    "dispute_rejected": 0xC8511C,     # EMBER orange
    # Story 9.3 — blacklist
    "user_banned": 0xD8231A,          # BUST red
    "user_unbanned": 0x5DBE5A,        # WIN green
    # Story 10.2 — config writes
    "config_changed": 0xF2B22A,       # GOLD
    # Story 10.6 — treasury operations
    "treasury_sweep": 0xF2B22A,       # GOLD
    "treasury_withdraw_to_user": 0xC8511C,  # EMBER (admin moves money out of treasury)
}


async def post_audit_event(
    *,
    pool: Executor,
    bot: discord.Client,
    action: str,
    title: str,
    description: str,
    actor_mention: str | None = None,
    target_mention: str | None = None,
    ticket_uid: str | None = None,
    amount: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> None:
    """Post a single audit event in ``#audit-log`` (best-effort).

    Args:
        action: a short slug used to colour the embed; one of the
            keys in ``_ACTION_COLOR``. Unknown actions get a
            HOUSE-blue fallback colour.
        title: the embed title (what happened, in 1 line).
        description: longer context (1-2 sentences).
        actor_mention: ``<@id>`` of who took the action.
        target_mention: ``<@id>`` of the affected user (if any).
        ticket_uid: e.g. ``deposit-12``.
        amount: gold amount in raw integer (rendered as ``50,000g``).
        extra_fields: additional ``{name: value}`` pairs to surface.

    Skips silently when ``#audit-log`` isn't configured (operator
    hasn't run ``/admin-setup`` with the new channel) or when the
    channel can no longer be resolved. Discord post failures are
    logged but never raised.
    """
    channel_id = await resolve_channel_id(pool, "audit_log")
    if channel_id is None:
        _log.info(
            "audit_log_skipped",
            reason="channel_id_unknown",
            action=action,
        )
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        _log.warning(
            "audit_log_skipped",
            reason="channel_not_found",
            action=action,
            channel_id=channel_id,
        )
        return

    color = _ACTION_COLOR.get(action, 0x5B7CC9)
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color(color),
        timestamp=datetime.now(UTC),
    )
    if ticket_uid:
        embed.add_field(name="Ticket", value=f"`{ticket_uid}`", inline=True)
    if amount is not None:
        embed.add_field(name="Amount", value=f"{amount:,}g", inline=True)
    if actor_mention:
        embed.add_field(name="Actor", value=actor_mention, inline=True)
    if target_mention and target_mention != actor_mention:
        embed.add_field(name="Target", value=target_mention, inline=True)
    if extra_fields:
        for k, v in extra_fields.items():
            embed.add_field(name=k, value=v, inline=False)

    try:
        await channel.send(embed=embed)  # type: ignore[union-attr]
        _log.info("audit_log_posted", action=action, ticket_uid=ticket_uid)
    except Exception as e:
        _log.exception("audit_log_failed", action=action, error=str(e))


# ---------------------------------------------------------------------------
# Convenience event types — narrow APIs each cog calls
# ---------------------------------------------------------------------------


TicketType = Literal["deposit", "withdraw"]


async def audit_ticket_opened(
    *,
    pool: Executor,
    bot: discord.Client,
    ticket_type: TicketType,
    ticket_uid: str,
    user_mention: str,
    amount: int,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="ticket_opened",
        title=f"📥 {ticket_type.capitalize()} ticket opened",
        description=f"{user_mention} opened a {ticket_type} ticket.",
        actor_mention=user_mention,
        ticket_uid=ticket_uid,
        amount=amount,
    )


async def audit_ticket_claimed(
    *,
    pool: Executor,
    bot: discord.Client,
    ticket_type: TicketType,
    ticket_uid: str,
    cashier_mention: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="ticket_claimed",
        title=f"🟡 {ticket_type.capitalize()} ticket claimed",
        description=f"{cashier_mention} claimed the ticket.",
        actor_mention=cashier_mention,
        ticket_uid=ticket_uid,
    )


async def audit_ticket_confirmed(
    *,
    pool: Executor,
    bot: discord.Client,
    ticket_type: TicketType,
    ticket_uid: str,
    cashier_mention: str,
    user_mention: str,
    amount: int,
    new_balance: int,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="ticket_confirmed",
        title=f"🟢 {ticket_type.capitalize()} confirmed",
        description=(
            f"{cashier_mention} confirmed for {user_mention}. "
            f"User balance is now **{new_balance:,}g**."
        ),
        actor_mention=cashier_mention,
        target_mention=user_mention,
        ticket_uid=ticket_uid,
        amount=amount,
    )


async def audit_ticket_cancelled(
    *,
    pool: Executor,
    bot: discord.Client,
    ticket_type: TicketType,
    ticket_uid: str,
    actor_mention: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="ticket_cancelled",
        title=f"❌ {ticket_type.capitalize()} cancelled",
        description=f"{actor_mention} cancelled the ticket. Reason: *{reason}*",
        actor_mention=actor_mention,
        ticket_uid=ticket_uid,
    )


async def audit_force_cashier_offline(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    cashier_mention: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="force_cashier_offline",
        title="🛑 Cashier forced offline",
        description=f"{admin_mention} forced {cashier_mention} offline. Reason: *{reason}*",
        actor_mention=admin_mention,
        target_mention=cashier_mention,
    )


async def audit_force_cancel_ticket(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    ticket_uid: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="force_cancel",
        title="🛑 Ticket force-cancelled by admin",
        description=f"{admin_mention} force-cancelled the ticket. Reason: *{reason}*",
        actor_mention=admin_mention,
        ticket_uid=ticket_uid,
    )


async def audit_force_close_thread(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    thread_mention: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="force_close_thread",
        title="🛑 Thread force-archived by admin",
        description=f"{admin_mention} archived {thread_mention}. Reason: *{reason}*",
        actor_mention=admin_mention,
    )


# ---------------------------------------------------------------------------
# Story 9.1 — dispute lifecycle posters
# ---------------------------------------------------------------------------


async def audit_dispute_opened(
    *,
    pool: Executor,
    bot: discord.Client,
    dispute_id: int,
    ticket_type: TicketType,
    ticket_uid: str,
    opener_mention: str,
    opener_role: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="dispute_opened",
        title=f"🚨 Dispute #{dispute_id} opened",
        description=(
            f"{opener_mention} ({opener_role}) opened a dispute on the "
            f"{ticket_type} ticket. Reason: *{reason}*"
        ),
        actor_mention=opener_mention,
        ticket_uid=ticket_uid,
        extra_fields={"Dispute ID": str(dispute_id), "Type": ticket_type},
    )


async def audit_dispute_resolved(
    *,
    pool: Executor,
    bot: discord.Client,
    dispute_id: int,
    ticket_uid: str,
    admin_mention: str,
    action: str,
    amount: int | None,
) -> None:
    extras: dict[str, str] = {"Dispute ID": str(dispute_id), "Action": action}
    description = (
        f"{admin_mention} resolved dispute #{dispute_id} as **{action}**."
    )
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="dispute_resolved",
        title=f"✅ Dispute #{dispute_id} resolved",
        description=description,
        actor_mention=admin_mention,
        ticket_uid=ticket_uid,
        amount=amount,
        extra_fields=extras,
    )


async def audit_dispute_rejected(
    *,
    pool: Executor,
    bot: discord.Client,
    dispute_id: int,
    ticket_uid: str,
    admin_mention: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="dispute_rejected",
        title=f"❌ Dispute #{dispute_id} rejected",
        description=f"{admin_mention} rejected the dispute. Reason: *{reason}*",
        actor_mention=admin_mention,
        ticket_uid=ticket_uid,
        extra_fields={"Dispute ID": str(dispute_id)},
    )


# ---------------------------------------------------------------------------
# Story 9.3 — blacklist posters
# ---------------------------------------------------------------------------


async def audit_user_banned(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    target_mention: str,
    reason: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="user_banned",
        title="🚫 User banned",
        description=f"{admin_mention} banned {target_mention}. Reason: *{reason}*",
        actor_mention=admin_mention,
        target_mention=target_mention,
    )


async def audit_user_unbanned(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    target_mention: str,
) -> None:
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="user_unbanned",
        title="✅ User unbanned",
        description=f"{admin_mention} unbanned {target_mention}.",
        actor_mention=admin_mention,
        target_mention=target_mention,
    )


# ---------------------------------------------------------------------------
# Story 10.2 / 10.3 — config-edit posters
# ---------------------------------------------------------------------------


async def audit_treasury_sweep(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    amount: int,
    new_balance: int,
    reason: str,
) -> None:
    """Posted on every successful ``/admin-treasury-sweep`` (Story 10.6)."""
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="treasury_sweep",
        title="🏦 Treasury sweep",
        description=(
            f"{admin_mention} swept **{amount:,}g** from the treasury. "
            f"New balance: **{new_balance:,}g**. Reason: *{reason}*"
        ),
        actor_mention=admin_mention,
        amount=amount,
        extra_fields={"New treasury balance": f"{new_balance:,}g"},
    )


async def audit_treasury_withdraw_to_user(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    target_mention: str,
    amount: int,
    reason: str,
) -> None:
    """Posted on every successful ``/admin-treasury-withdraw-to-user``."""
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="treasury_withdraw_to_user",
        title="🏦 Treasury withdraw to user",
        description=(
            f"{admin_mention} sent **{amount:,}g** from the treasury to "
            f"{target_mention}. Reason: *{reason}*"
        ),
        actor_mention=admin_mention,
        target_mention=target_mention,
        amount=amount,
    )


async def audit_config_changed(
    *,
    pool: Executor,
    bot: discord.Client,
    admin_mention: str,
    key: str,
    new_value: str,
    old_value: str | None = None,
) -> None:
    """Generic config-change poster used by every ``/admin-set-*`` command.

    The ``new_value`` and ``old_value`` are stringified by the caller so
    a single poster handles ints (limits, fees), text (guides), even
    JSON (``dynamic_embeds.fields``). Old value is optional because some
    callers don't read the prior row before overwriting.
    """
    description_parts = [f"{admin_mention} updated `{key}`."]
    if old_value is not None:
        description_parts.append(f"Old: `{old_value}`")
    description_parts.append(f"New: `{new_value}`")
    await post_audit_event(
        pool=pool,
        bot=bot,
        action="config_changed",
        title=f"⚙️ Config changed — `{key}`",
        description=" ".join(description_parts),
        actor_mention=admin_mention,
        extra_fields={"Key": key, "New value": new_value},
    )


__all__ = [
    "audit_config_changed",
    "audit_dispute_opened",
    "audit_dispute_rejected",
    "audit_dispute_resolved",
    "audit_force_cancel_ticket",
    "audit_force_cashier_offline",
    "audit_force_close_thread",
    "audit_ticket_cancelled",
    "audit_ticket_claimed",
    "audit_ticket_confirmed",
    "audit_ticket_opened",
    "audit_treasury_sweep",
    "audit_treasury_withdraw_to_user",
    "audit_user_banned",
    "audit_user_unbanned",
    "post_audit_event",
]
