"""Unit tests for `deathroll_core.embeds.account`.

Three builders live in this module:

- ``account_summary_embed`` — the ephemeral response for ``/balance``.
- ``no_balance_embed`` — shown when a user with no ``core.users`` row
  runs ``/balance``; the spec marks it shared with Luck.
- ``help_embed`` — the ``/help`` topic dispatcher.
"""

from __future__ import annotations

import discord
import pytest
from deathroll_core.embeds.account import (
    HELP_TOPICS,
    account_summary_embed,
    help_embed,
    no_balance_embed,
)

# ---------------------------------------------------------------------------
# account_summary_embed
# ---------------------------------------------------------------------------


def test_account_summary_renders_all_four_metrics() -> None:
    embed = account_summary_embed(
        balance=75_000,
        total_deposited=250_000,
        total_withdrawn=175_000,
        lifetime_fee_paid=3_500,
    )
    blob = (embed.title or "") + (embed.description or "") + " ".join(
        f.value or "" for f in embed.fields
    )
    assert "75,000" in blob
    assert "250,000" in blob
    assert "175,000" in blob
    assert "3,500" in blob


def test_account_summary_zero_balance_renders_cleanly() -> None:
    embed = account_summary_embed(
        balance=0, total_deposited=0, total_withdrawn=0, lifetime_fee_paid=0
    )
    assert isinstance(embed, discord.Embed)
    blob = " ".join(f.value or "" for f in embed.fields)
    assert "0" in blob


# ---------------------------------------------------------------------------
# no_balance_embed
# ---------------------------------------------------------------------------


def test_no_balance_embed_redirects_to_how_to_deposit() -> None:
    """A user with no balance must be told where to go to deposit."""
    embed = no_balance_embed(deposit_channel_mention="<#111>")
    body = (embed.description or "") + (embed.title or "")
    assert "<#111>" in body or "deposit" in body.lower()


def test_no_balance_embed_default_mention() -> None:
    """If no mention is supplied (e.g., on a fresh server), fall back
    to a literal ``#how-to-deposit`` reference rather than crashing."""
    embed = no_balance_embed()
    body = (embed.description or "")
    assert "how-to-deposit" in body or "deposit" in body.lower()


# ---------------------------------------------------------------------------
# help_embed
# ---------------------------------------------------------------------------


def test_help_topics_constant_lists_canonical_topics() -> None:
    """Spec §5.1: deposit, withdraw, fairness, support."""
    assert set(HELP_TOPICS.keys()) == {"deposit", "withdraw", "fairness", "support"}


def test_help_embed_with_no_topic_lists_all_topics() -> None:
    embed = help_embed()
    field_names = {f.name for f in embed.fields}
    assert {"deposit", "withdraw", "fairness", "support"}.issubset(field_names)


@pytest.mark.parametrize("topic", ["deposit", "withdraw", "fairness", "support"])
def test_help_embed_for_known_topic_renders_topic_specific_content(topic: str) -> None:
    embed = help_embed(topic=topic)
    title = embed.title or ""
    desc = embed.description or ""
    # Either the title or description must reference the requested topic.
    assert topic in title.lower() or topic in desc.lower()


def test_help_embed_unknown_topic_falls_back_to_overview() -> None:
    """A garbage topic should not crash; surface the topic list instead."""
    embed = help_embed(topic="nonsense")
    field_names = {f.name for f in embed.fields}
    assert {"deposit", "withdraw", "fairness", "support"}.issubset(field_names)
