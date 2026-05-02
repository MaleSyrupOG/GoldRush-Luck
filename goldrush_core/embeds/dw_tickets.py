"""Embed builders for the Deposit/Withdraw bot.

Every visual surface the D/W bot puts in front of users or cashiers is
constructed from a builder in this module. Builders are pure functions
that take the data needed to render and return a fresh
``discord.Embed``. They never read the database, never speak to the
network, never mutate state — which makes them trivially testable as
snapshots and deterministic across redeploys.

The visual contract is the screenshot Aleix captured from the
incumbent bot on 2026-05-01 (memorialised in
``reference_deposit_ticket_ux.md``):

- The deposit ticket flow uses 5 colour-coded states: HOUSE blue for
  "Submitted", BUST red when no cashier is online for the user's
  region+faction, EMBER orange for the "please wait, do not trade
  yet" reminder, GOLD when a cashier claims the ticket, and WIN green
  for the final confirmed state.
- The claimed embed always carries the anti-phishing warning
  ``⚠️ The cashier will NEVER send a trade request first — you
  approach them.`` This is critical UX: it is the textual rule the
  user is held to when assessing whether an in-game whisper is
  legitimate.
- Region "NA" is displayed as ``(US)`` in the user-facing copy.
- Amounts are rendered with thousands separators and a trailing
  ``g`` (e.g., ``50,000g``). The DB stores them as ``BIGINT``.

The withdraw flow mirrors the deposit flow visually but has two
additions: the open embed shows ``amount`` / ``fee`` /
``amount_delivered`` (so the user sees the fee deduction up-front),
and the cancelled embed announces ``REFUNDED`` because the locked
balance is restored on cancel.

Disputes, treasury, cashier admin embeds round out the set. Together
they cover the 14 builders in spec §5.6 plus the two helper embeds
(``awaiting_cashier_embed``, ``wait_instructions_embed``) demanded by
the visual contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

import discord

# ---------------------------------------------------------------------------
# Colour palette — inherited from the GoldRush design system (Luck §6.3)
# ---------------------------------------------------------------------------

# Hex values, stored as plain ints for use with ``discord.Color(int)``.
COLOR_INK = 0x06060B
COLOR_GOLD = 0xF2B22A
COLOR_WIN = 0x5DBE5A
COLOR_BUST = 0xD8231A
COLOR_EMBER = 0xC8511C
COLOR_HOUSE = 0x5B7CC9
COLOR_JACKPOT = 0xFFD800


# Constants kept in module scope so they appear in every builder that
# needs them and so tests can assert against the canonical wording.
TicketType = Literal["deposit", "withdraw"]

_ANTI_PHISHING = (
    "⚠️ The cashier will NEVER send a trade request first — you approach them."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_g(amount: int) -> str:
    """Render an integer amount as ``50,000g`` with thousand separators.

    The trailing ``g`` is the GoldRush in-game currency unit (see
    ``project_economics.md`` in MEMORY).
    """
    return f"{amount:,}g"


def _region_label(region: str) -> str:
    """Map our internal region code to the user-visible label.

    Per the visual contract (and per common WoW vernacular), users
    expect to see ``(US)`` not ``(NA)`` next to a US realm. We keep
    the storage value ``NA`` (matches the DB enum) and translate at
    the UI boundary only.
    """
    if region == "NA":
        return "US"
    return region


def _row_break() -> tuple[str, str, bool]:
    """Return the args for a zero-width-space inline field.

    Discord lays out inline embed fields in groups of three; inserting
    a single ``​`` field after two real fields forces a row
    break so the next row starts at column 1. Used by the open embed
    so ``Deposit ID`` and ``Status`` sit on a row of their own.
    """
    return ("​", "​", True)


# ---------------------------------------------------------------------------
# Deposit ticket lifecycle
# ---------------------------------------------------------------------------


def deposit_ticket_open_embed(
    *,
    ticket_uid: str,
    char_name: str,
    region: str,
    faction: str,
    amount: int,
    created_at: datetime,
) -> discord.Embed:
    """Initial 'Submitted' embed posted in a freshly-created deposit ticket channel."""
    embed = discord.Embed(
        title="Deposit — 🔵 Submitted",
        color=discord.Color(COLOR_HOUSE),
        timestamp=created_at,
    )
    embed.add_field(name="Deposit ID", value=ticket_uid, inline=True)
    embed.add_field(name="Status", value="🔵 Submitted", inline=True)
    rb_name, rb_value, rb_inline = _row_break()
    embed.add_field(name=rb_name, value=rb_value, inline=rb_inline)
    embed.add_field(name="Character", value=char_name, inline=True)
    embed.add_field(name="Region", value=region, inline=True)
    embed.add_field(name="Faction", value=faction, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=False)
    return embed


def awaiting_cashier_embed(
    *,
    region: str,
    faction: str,
    ticket_type: TicketType,
) -> discord.Embed:
    """Posted when no cashier is online for the user's (region, faction).

    The user is asked to stay in the channel — the FIFO worker will
    auto-claim once a matching cashier transitions to ``online``.
    """
    embed = discord.Embed(
        title=f"🔴 No {region} {faction} cashiers online",
        description=(
            f"Your {ticket_type} ticket is open. "
            f"A cashier will claim it when one comes online for your region/faction.\n"
            f"\n**Please stay in this channel.**"
        ),
        color=discord.Color(COLOR_BUST),
    )
    return embed


def wait_instructions_embed(*, ticket_type: TicketType) -> discord.Embed:
    """Anti-scam reminder posted alongside the open embed.

    Goal: prevent the user from sending gold to a stranger that DMs
    them claiming to be a cashier before the legitimate cashier has
    claimed the ticket in-channel. The phrasing must explicitly say
    ``do not trade gold`` so it is impossible to misread.
    """
    embed = discord.Embed(
        title="⏳ Please wait for a cashier to claim your ticket.",
        description=(
            "Do not trade gold until a cashier gives you instructions here."
        ),
        color=discord.Color(COLOR_EMBER),
    )
    # ticket_type is accepted for symmetry with the rest of the API; the
    # text is identical for deposits and withdraws because the warning
    # itself is identical.
    _ = ticket_type
    return embed


def deposit_ticket_claimed_embed(
    *,
    ticket_uid: str,
    amount: int,
    user_char_name: str,
    cashier_mention: str,
    cashier_char: str,
    cashier_realm: str,
    cashier_region: str,
    location: str,
) -> discord.Embed:
    """Posted when a cashier claims the deposit; user must approach in-game."""
    region_label = _region_label(cashier_region)
    embed = discord.Embed(
        title="🟡 Ticket Claimed",
        description=(
            f"{cashier_mention} has claimed your ticket "
            f"(active in **{cashier_realm}** ({region_label})).\n"
            f"**Please follow the Cashier's instructions!**\n\n"
            f"{_ANTI_PHISHING}"
        ),
        color=discord.Color(COLOR_GOLD),
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=True)
    embed.add_field(name="Character", value=cashier_char, inline=True)
    embed.add_field(name="Location", value=location, inline=False)
    # user_char_name is accepted so callers can pass a fully-typed
    # ticket; we don't display it because the visual contract uses the
    # cashier's char as the "Character" field. Kept as kw-only so future
    # iterations can surface it without breaking signatures.
    _ = user_char_name
    return embed


def deposit_ticket_confirmed_embed(
    *,
    ticket_uid: str,
    amount: int,
    new_balance: int,
    confirmed_at: datetime,
) -> discord.Embed:
    """Final happy-path embed; balance has been credited."""
    embed = discord.Embed(
        title="🟢 Deposit Confirmed!",
        description=f"Your deposit of **{_format_g(amount)}** has been credited.",
        color=discord.Color(COLOR_WIN),
        timestamp=confirmed_at,
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Amount Credited", value=_format_g(amount), inline=True)
    embed.add_field(name="New Balance", value=_format_g(new_balance), inline=True)
    return embed


def deposit_ticket_cancelled_embed(
    *,
    ticket_uid: str,
    reason: str,
    cancelled_at: datetime,
) -> discord.Embed:
    """Terminal cancel — no balance change for deposits."""
    embed = discord.Embed(
        title="❌ Deposit Cancelled",
        description=f"Reason: {reason}",
        color=discord.Color(COLOR_BUST),
        timestamp=cancelled_at,
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    return embed


# ---------------------------------------------------------------------------
# Withdraw ticket lifecycle
# ---------------------------------------------------------------------------


def withdraw_ticket_open_embed(
    *,
    ticket_uid: str,
    char_name: str,
    region: str,
    faction: str,
    amount: int,
    fee: int,
    amount_delivered: int,
    created_at: datetime,
) -> discord.Embed:
    """Initial embed for a withdraw — must show fee + amount_delivered upfront.

    The user sees three values:
    - ``Amount`` — gross requested (what the system locked)
    - ``Fee`` — captured at open time, fixed for this ticket
    - ``Delivered`` — what the cashier will trade in-game
    """
    embed = discord.Embed(
        title="Withdraw — 🔵 Submitted",
        color=discord.Color(COLOR_HOUSE),
        timestamp=created_at,
    )
    embed.add_field(name="Withdraw ID", value=ticket_uid, inline=True)
    embed.add_field(name="Status", value="🔵 Submitted", inline=True)
    rb_name, rb_value, rb_inline = _row_break()
    embed.add_field(name=rb_name, value=rb_value, inline=rb_inline)
    embed.add_field(name="Character", value=char_name, inline=True)
    embed.add_field(name="Region", value=region, inline=True)
    embed.add_field(name="Faction", value=faction, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=True)
    embed.add_field(name="Fee", value=_format_g(fee), inline=True)
    embed.add_field(name="Delivered", value=_format_g(amount_delivered), inline=True)
    return embed


def withdraw_ticket_claimed_embed(
    *,
    ticket_uid: str,
    amount: int,
    amount_delivered: int,
    user_char_name: str,
    cashier_mention: str,
    cashier_char: str,
    cashier_realm: str,
    cashier_region: str,
    location: str,
) -> discord.Embed:
    """Posted when a cashier claims the withdraw; user must approach in-game.

    Same anti-phishing rule as the deposit claimed embed: the cashier
    initiates the in-game whisper at the published location, and the
    user opens the trade.
    """
    region_label = _region_label(cashier_region)
    embed = discord.Embed(
        title="🟡 Withdraw Claimed",
        description=(
            f"{cashier_mention} has claimed your withdraw "
            f"(active in **{cashier_realm}** ({region_label})).\n"
            f"**Please follow the Cashier's instructions!**\n\n"
            f"{_ANTI_PHISHING}"
        ),
        color=discord.Color(COLOR_GOLD),
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=True)
    embed.add_field(name="Delivered", value=_format_g(amount_delivered), inline=True)
    embed.add_field(name="Cashier Character", value=cashier_char, inline=True)
    embed.add_field(name="Location", value=location, inline=False)
    _ = user_char_name
    return embed


def withdraw_ticket_confirmed_embed(
    *,
    ticket_uid: str,
    amount: int,
    fee: int,
    amount_delivered: int,
    new_balance: int,
    confirmed_at: datetime,
) -> discord.Embed:
    """Final happy-path embed for a withdraw."""
    embed = discord.Embed(
        title="🟢 Withdraw Confirmed!",
        description=f"You have received **{_format_g(amount_delivered)}** ingame.",
        color=discord.Color(COLOR_WIN),
        timestamp=confirmed_at,
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=True)
    embed.add_field(name="Fee", value=_format_g(fee), inline=True)
    embed.add_field(name="Delivered", value=_format_g(amount_delivered), inline=True)
    embed.add_field(name="New Balance", value=_format_g(new_balance), inline=True)
    return embed


def withdraw_ticket_cancelled_embed(
    *,
    ticket_uid: str,
    refunded_amount: int,
    reason: str,
    cancelled_at: datetime,
) -> discord.Embed:
    """Cancelled withdraw — locked balance has been refunded.

    The title carries the ``REFUNDED`` indicator so the user reads it
    at a glance even before opening the embed body. This is essential
    for trust: a cancel without an explicit refund acknowledgement is
    a flagged dispute risk.
    """
    embed = discord.Embed(
        title="❌ Withdraw Cancelled — REFUNDED",
        description=(
            f"Your withdraw was cancelled and **{_format_g(refunded_amount)}** "
            f"has been REFUNDED to your balance.\n"
            f"\nReason: {reason}"
        ),
        color=discord.Color(COLOR_BUST),
        timestamp=cancelled_at,
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Refunded", value=_format_g(refunded_amount), inline=True)
    return embed


# ---------------------------------------------------------------------------
# Cashier-side embeds
# ---------------------------------------------------------------------------


def cashier_alert_embed(
    *,
    ticket_uid: str,
    ticket_type: TicketType,
    region: str,
    faction: str,
    amount: int,
    channel_mention: str,
) -> discord.Embed:
    """Posted in ``#cashier-alerts`` (or equivalent) so cashiers see new tickets fast."""
    embed = discord.Embed(
        title=f"🔔 New {ticket_type} ticket",
        description=(
            f"{ticket_uid} — {region} {faction} — {_format_g(amount)}\n"
            f"Channel: {channel_mention}"
        ),
        color=discord.Color(COLOR_GOLD),
    )
    embed.add_field(name="ID", value=ticket_uid, inline=True)
    embed.add_field(name="Type", value=ticket_type, inline=True)
    embed.add_field(name="Amount", value=_format_g(amount), inline=True)
    embed.add_field(name="Region", value=region, inline=True)
    embed.add_field(name="Faction", value=faction, inline=True)
    embed.add_field(name="Open in", value=channel_mention, inline=True)
    return embed


def online_cashiers_live_embed(
    *,
    cashiers: Sequence[Mapping[str, Any]],
    last_updated: datetime,
) -> discord.Embed:
    """Live roster posted in ``#online-cashiers``; refreshed every 30 s.

    Each entry in ``cashiers`` is a dict with at least the keys
    ``mention``, ``region``, ``faction``, ``status`` and (optionally)
    ``location``. Empty rosters render an explicit "No cashiers"
    message rather than a blank embed so the channel is always
    informative.
    """
    if not cashiers:
        return discord.Embed(
            title="Online cashiers",
            description="No cashiers are currently online.",
            color=discord.Color(COLOR_EMBER),
            timestamp=last_updated,
        )
    lines: list[str] = []
    for c in cashiers:
        loc = c.get("location") or "—"
        lines.append(
            f"{c['mention']} · {c['region']} {c['faction']} · "
            f"{c['status']} · {loc}"
        )
    return discord.Embed(
        title="Online cashiers",
        description="\n".join(lines),
        color=discord.Color(COLOR_WIN),
        timestamp=last_updated,
    )


def cashier_stats_embed(
    *,
    cashier_mention: str,
    deposits_completed: int,
    deposits_cancelled: int,
    withdraws_completed: int,
    withdraws_cancelled: int,
    total_volume_g: int,
    total_online_seconds: int,
    avg_claim_to_confirm_s: int | None,
    last_active_at: datetime | None,
) -> discord.Embed:
    """Admin ephemeral — surface every metric tracked in ``dw.cashier_stats``."""
    online_hours = total_online_seconds / 3600
    embed = discord.Embed(
        title="Cashier stats",
        description=f"Stats for {cashier_mention}",
        color=discord.Color(COLOR_HOUSE),
        timestamp=last_active_at,
    )
    embed.add_field(name="Deposits done", value=str(deposits_completed), inline=True)
    embed.add_field(name="Deposits cancelled", value=str(deposits_cancelled), inline=True)
    embed.add_field(name="Withdraws done", value=str(withdraws_completed), inline=True)
    embed.add_field(name="Withdraws cancelled", value=str(withdraws_cancelled), inline=True)
    embed.add_field(name="Volume", value=_format_g(total_volume_g), inline=True)
    embed.add_field(name="Online", value=f"{online_hours:.1f} h", inline=True)
    avg_str = f"{avg_claim_to_confirm_s} s" if avg_claim_to_confirm_s is not None else "—"
    embed.add_field(name="Avg claim→confirm", value=avg_str, inline=True)
    last_active_str = (
        last_active_at.strftime("%Y-%m-%d %H:%M UTC") if last_active_at else "never"
    )
    embed.add_field(name="Last active", value=last_active_str, inline=True)
    return embed


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


def dispute_open_embed(
    *,
    dispute_id: int,
    ticket_uid: str,
    ticket_type: TicketType,
    opener_mention: str,
    opener_role: Literal["admin", "user", "system"],
    reason: str,
    opened_at: datetime,
) -> discord.Embed:
    """Posted in ``#disputes`` when a user, admin, or system opens a dispute."""
    embed = discord.Embed(
        title=f"🚨 Dispute #{dispute_id} opened",
        description=f"On ticket `{ticket_uid}` ({ticket_type})",
        color=discord.Color(COLOR_BUST),
        timestamp=opened_at,
    )
    embed.add_field(name="Dispute ID", value=str(dispute_id), inline=True)
    embed.add_field(name="Ticket", value=ticket_uid, inline=True)
    embed.add_field(name="Type", value=ticket_type, inline=True)
    embed.add_field(name="Opened by", value=f"{opener_mention} ({opener_role})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed


def dispute_resolved_embed(
    *,
    dispute_id: int,
    ticket_uid: str,
    resolution: str,
    resolved_by_mention: str,
    resolved_at: datetime,
    status: Literal["resolved", "rejected"],
) -> discord.Embed:
    """Final dispute embed — colour reflects whether the claim was upheld."""
    color = COLOR_WIN if status == "resolved" else COLOR_BUST
    icon = "✅" if status == "resolved" else "❌"
    embed = discord.Embed(
        title=f"{icon} Dispute #{dispute_id} {status}",
        description=resolution,
        color=discord.Color(color),
        timestamp=resolved_at,
    )
    embed.add_field(name="Ticket", value=ticket_uid, inline=True)
    embed.add_field(name="Resolved by", value=resolved_by_mention, inline=True)
    return embed


# ---------------------------------------------------------------------------
# Dynamic / config-driven
# ---------------------------------------------------------------------------


def _parse_color_hex(value: str) -> int | None:
    """Best-effort parse of ``#RRGGBB`` (or ``RRGGBB``) into an int.

    Validation already happens upstream in ``EditDynamicEmbedInput``;
    this is a defence-in-depth check so a corrupt DB row does not
    panic the renderer at runtime.
    """
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        return None
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


def _parse_fields_json(value: str | None) -> list[dict[str, Any]]:
    """Parse a ``[{"name": str, "value": str, "inline": bool}, ...]`` JSON blob.

    Returns ``[]`` on any parse failure so the embed still renders
    without fields (rather than raising and leaving the channel
    embed-less).
    """
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and "name" in item and "value" in item:
            out.append(
                {
                    "name": str(item["name"]),
                    "value": str(item["value"]),
                    "inline": bool(item.get("inline", False)),
                }
            )
    return out


def how_to_deposit_dynamic_embed(
    *,
    title: str,
    description: str,
    color_hex: str | None = None,
    fields_json: str | None = None,
    image_url: str | None = None,
    footer_text: str | None = None,
) -> discord.Embed:
    """Render an embed described by a row in ``dw.dynamic_embeds``.

    The same builder serves both ``how_to_deposit`` and
    ``how_to_withdraw`` rows because the shape is identical; the row
    key just selects which content is being rendered.
    """
    color_int: int | None = _parse_color_hex(color_hex) if color_hex else COLOR_HOUSE
    if color_int is None:
        color_int = COLOR_HOUSE
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color(color_int),
    )
    for f in _parse_fields_json(fields_json):
        embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
    if image_url:
        embed.set_image(url=image_url)
    if footer_text:
        embed.set_footer(text=footer_text)
    return embed


# ---------------------------------------------------------------------------
# Treasury
# ---------------------------------------------------------------------------


def treasury_balance_embed(
    *,
    balance: int,
    last_sweep_at: datetime | None,
    last_sweep_amount: int | None,
) -> discord.Embed:
    """Admin ephemeral — current treasury balance + last sweep info."""
    embed = discord.Embed(
        title="Treasury balance",
        color=discord.Color(COLOR_GOLD),
    )
    embed.add_field(name="Balance", value=_format_g(balance), inline=False)
    if last_sweep_at and last_sweep_amount is not None:
        embed.add_field(
            name="Last sweep",
            value=(
                f"{_format_g(last_sweep_amount)} on "
                f"{last_sweep_at.strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            inline=False,
        )
    else:
        embed.add_field(name="Last sweep", value="No sweeps recorded yet.", inline=False)
    return embed
