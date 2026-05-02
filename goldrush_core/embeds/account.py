"""Account-related embed builders shared with Luck.

Three pure-function builders live here:

- ``account_summary_embed``: the ephemeral ``/balance`` response
  showing balance + deposit / withdraw / fee totals.
- ``no_balance_embed``: shown to a user who runs ``/balance`` (or
  any account-aware command) before they have a ``core.users`` row.
  Spec §5.6 marks this embed as shared with Luck — both bots show
  it identically when a new user appears.
- ``help_embed``: dispatches the ``/help`` topic argument to a
  topic-specific embed; without an argument it lists the topics.

The text content here is intentionally short and stable. Long-form
guides live in the editable ``dw.dynamic_embeds`` rows rendered by
``how_to_deposit_dynamic_embed`` so admins can update without a
redeploy.
"""

from __future__ import annotations

import discord

from goldrush_core.embeds.dw_tickets import (
    COLOR_BUST,
    COLOR_GOLD,
    COLOR_HOUSE,
)


def _format_g(amount: int) -> str:
    """Mirror of the helper in dw_tickets — same format everywhere."""
    return f"{amount:,}g"


# Ordered so /help renders topics in the same sequence every boot.
HELP_TOPICS: dict[str, tuple[str, str]] = {
    "deposit": (
        "How to deposit",
        (
            "Run ``/deposit`` in #deposit and follow the cashier's "
            "instructions in your private ticket channel. The cashier "
            "will tell you where to meet in-game; you approach them, "
            "never the other way around."
        ),
    ),
    "withdraw": (
        "How to withdraw",
        (
            "Run ``/withdraw`` in #withdraw. The bot locks the requested "
            "amount on your balance and a cashier claims your ticket. "
            "After the in-game trade, ``/confirm`` finalises and the "
            "fee is taken from the gross amount."
        ),
    ),
    "fairness": (
        "Provably Fair",
        (
            "All gambling outcomes are provably fair via HMAC-SHA512 "
            "with per-user seeds. See #fairness for the verifier and "
            "your current seed pair."
        ),
    ),
    "support": (
        "Support",
        (
            "For account or dispute issues, use ``/admin dispute open`` "
            "with your ticket UID and a clear description, or DM the "
            "@admin role for emergencies."
        ),
    ),
}


def account_summary_embed(
    *,
    balance: int,
    total_deposited: int,
    total_withdrawn: int,
    lifetime_fee_paid: int,
) -> discord.Embed:
    """Render the ``/balance`` ephemeral embed.

    Four fields, GOLD accent. The user reads this in chat
    (ephemeral) and confirms their financial picture before
    initiating another deposit / withdraw.
    """
    embed = discord.Embed(
        title="Your account",
        color=discord.Color(COLOR_GOLD),
    )
    embed.add_field(name="Balance", value=_format_g(balance), inline=False)
    embed.add_field(
        name="Total deposited", value=_format_g(total_deposited), inline=True
    )
    embed.add_field(
        name="Total withdrawn", value=_format_g(total_withdrawn), inline=True
    )
    embed.add_field(
        name="Lifetime fees paid",
        value=_format_g(lifetime_fee_paid),
        inline=True,
    )
    return embed


def no_balance_embed(
    *,
    deposit_channel_mention: str = "#how-to-deposit",
) -> discord.Embed:
    """Render the redirect embed for users with no ``core.users`` row.

    Spec §5.6 calls this out as shared with Luck. When a new user
    runs any account-aware command (``/balance`` here; in Luck a
    bet command), they see this embed pointing them at the deposit
    flow.
    """
    return discord.Embed(
        title="No balance yet",
        description=(
            f"You don't have a GoldRush balance yet.\n"
            f"See {deposit_channel_mention} for instructions on how to "
            f"deposit gold and get started."
        ),
        color=discord.Color(COLOR_HOUSE),
    )


def help_embed(*, topic: str | None = None) -> discord.Embed:
    """Render the ``/help`` embed for a given topic, or the topic list.

    An unknown ``topic`` falls back to the topic list rather than
    raising — defensive against autocomplete / typo edge cases.
    """
    if topic is None or topic not in HELP_TOPICS:
        embed = discord.Embed(
            title="Help",
            description="Pick a topic with ``/help <topic>``.",
            color=discord.Color(COLOR_HOUSE),
        )
        for key, (title, _body) in HELP_TOPICS.items():
            embed.add_field(name=key, value=title, inline=False)
        return embed

    title, body = HELP_TOPICS[topic]
    return discord.Embed(
        title=title,
        description=body,
        color=discord.Color(COLOR_GOLD),
    )


__all__ = [
    "HELP_TOPICS",
    "account_summary_embed",
    "help_embed",
    "no_balance_embed",
]


# Mark COLOR_BUST as referenced — it's exported here so future builders
# (e.g., banned-user redirect embed) can import a single set of colours
# from this module instead of digging into dw_tickets.
_ = COLOR_BUST
