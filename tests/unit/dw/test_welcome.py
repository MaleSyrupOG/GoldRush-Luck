"""Unit tests for `goldrush_deposit_withdraw.welcome` (Story 4.4).

The reconciler must:

- Be idempotent: running it on a fully-seeded state edits in place
  rather than reposting.
- Self-heal: if the stored ``message_id`` no longer exists on
  Discord (admin deleted it), repost and update the row.
- Skip gracefully when the channel id has not yet been persisted
  (operator hasn't run ``/admin setup`` yet).

Tests use in-process fakes for the asyncpg pool and the
``discord.py`` channel / message API surface — no Discord required.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
import pytest
from goldrush_deposit_withdraw.welcome import (
    DEFAULT_WELCOMES,
    ReconcileOutcome,
    WelcomeDefault,
    reconcile_welcome_embed,
    reconcile_welcome_embeds,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, message_id: int = 555) -> None:
        self.id = message_id
        self.edit_calls: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.edit_calls.append(kwargs)


class _FakeChannel:
    """In-memory fake for ``discord.TextChannel``.

    ``send`` returns a new ``_FakeMessage`` whose id increments per
    call. ``fetch_message`` returns a stored message or raises
    ``discord.NotFound`` if the id is unknown.
    """

    def __init__(self, channel_id: int = 100) -> None:
        self.id = channel_id
        self.messages: dict[int, _FakeMessage] = {}
        self._next_id = 555

    async def send(self, *, embed: discord.Embed) -> _FakeMessage:
        msg = _FakeMessage(message_id=self._next_id)
        self.messages[self._next_id] = msg
        self._next_id += 1
        return msg

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        if message_id in self.messages:
            return self.messages[message_id]
        # Real discord.py raises NotFound; we mirror with a constructed
        # one. discord.NotFound takes (response, message), so we use
        # the simplest possible synthetic.
        raise discord.NotFound(
            response=_FakeResponse(),  # type: ignore[arg-type]
            message=f"Unknown message id {message_id}",
        )


class _FakeResponse:
    """Minimum shape for discord.NotFound's response argument."""

    status = 404
    reason = "Not Found"


class _FakeBot:
    def __init__(self, channels: dict[int, _FakeChannel]) -> None:
        self._channels = channels

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channels.get(channel_id)


class _FakePool:
    """Bare-minimum asyncpg-shaped pool driven from a row store."""

    def __init__(
        self,
        rows: dict[str, dict[str, Any]] | None = None,
        config: dict[str, int] | None = None,
    ) -> None:
        self.rows = rows or {}
        self.config = config or {}
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> dict[str, Any] | None:
        if "FROM dw.dynamic_embeds" in query:
            embed_key = args[0]
            return self.rows.get(embed_key)
        if "FROM dw.global_config" in query:
            key = args[0]
            if key in self.config:
                return {"value_int": self.config[key]}
            return None
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        self.executes.append((query, args))
        if "INSERT INTO dw.dynamic_embeds" in query:
            embed_key = args[0]
            self.rows[embed_key] = {
                "embed_key": embed_key,
                "channel_id": args[1],
                "message_id": None,
                "title": args[2],
                "description": args[3],
                "color_hex": "#F2B22A",
                "fields": [],
                "image_url": None,
                "footer_text": None,
            }
        elif "UPDATE dw.dynamic_embeds" in query and "message_id" in query:
            new_id = args[0]
            embed_key = args[1]
            self.rows[embed_key]["message_id"] = new_id
        return "OK"


# ---------------------------------------------------------------------------
# Spec sanity
# ---------------------------------------------------------------------------


def test_default_welcomes_covers_canonical_keys() -> None:
    """Spec §5.6: ``how_to_deposit``, ``how_to_withdraw``, and
    ``cashier_onboarding`` are the canonical dynamic embeds the bot
    renders on startup. Cashier onboarding was added later so new
    cashiers get a procedure cheatsheet without an admin having to
    paste it manually every time the channel is provisioned."""
    keys = {w.embed_key for w in DEFAULT_WELCOMES}
    assert keys == {"how_to_deposit", "how_to_withdraw", "cashier_onboarding"}


def test_cashier_onboarding_default_mentions_anti_phishing_rule() -> None:
    """The single most-important rule cashiers must internalize is the
    anti-phishing one: they NEVER initiate the trade. The default copy
    must bake this in so new cashiers see it on first walk-through."""
    onboarding = next(
        w for w in DEFAULT_WELCOMES if w.embed_key == "cashier_onboarding"
    )
    text = (onboarding.title + " " + onboarding.description).lower()
    # Either phrasing satisfies — we keep flexibility for future copy
    # tweaks, but one of them must be present.
    assert "never" in text and ("trade" in text or "approach" in text)


def test_welcome_default_is_frozen() -> None:
    import dataclasses

    w = DEFAULT_WELCOMES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.title = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# reconcile_welcome_embed — single-key scenarios
# ---------------------------------------------------------------------------


def test_first_run_inserts_row_and_posts_message() -> None:
    """No row, no message — happy path: insert + send + record id."""
    pool = _FakePool(rows={}, config={})
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    async def _exercise() -> ReconcileOutcome:
        return await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=100,
            default_title="How to deposit",
            default_description="Run /deposit and follow the cashier.",
        )

    outcome = asyncio.run(_exercise())
    assert outcome.action == "posted"
    assert outcome.message_id is not None
    # Row was inserted then the message id was UPDATEd back.
    assert "how_to_deposit" in pool.rows
    assert pool.rows["how_to_deposit"]["message_id"] == outcome.message_id


def test_existing_row_with_message_id_edits_in_place() -> None:
    """Idempotent path: row exists, message exists → edit, no insert."""
    channel = _FakeChannel(channel_id=100)
    existing_msg = _FakeMessage(message_id=42)
    channel.messages[42] = existing_msg
    bot = _FakeBot(channels={100: channel})
    pool = _FakePool(
        rows={
            "how_to_deposit": {
                "embed_key": "how_to_deposit",
                "channel_id": 100,
                "message_id": 42,
                "title": "How to deposit",
                "description": "Edited content",
                "color_hex": "#F2B22A",
                "fields": [],
                "image_url": None,
                "footer_text": None,
            }
        }
    )

    async def _exercise() -> ReconcileOutcome:
        return await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=100,
            default_title="ignored",
            default_description="ignored",
        )

    outcome = asyncio.run(_exercise())
    assert outcome.action == "edited"
    assert outcome.message_id == 42
    assert len(existing_msg.edit_calls) == 1


def test_message_deleted_on_discord_side_triggers_repost() -> None:
    """A row with message_id=42 but no message on Discord → catch
    NotFound, send a fresh message, UPDATE the row."""
    channel = _FakeChannel(channel_id=100)
    # No message preloaded → fetch_message will raise NotFound.
    bot = _FakeBot(channels={100: channel})
    pool = _FakePool(
        rows={
            "how_to_deposit": {
                "embed_key": "how_to_deposit",
                "channel_id": 100,
                "message_id": 42,
                "title": "How to deposit",
                "description": "x",
                "color_hex": "#F2B22A",
                "fields": [],
                "image_url": None,
                "footer_text": None,
            }
        }
    )

    async def _exercise() -> ReconcileOutcome:
        return await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=100,
            default_title="ignored",
            default_description="ignored",
        )

    outcome = asyncio.run(_exercise())
    assert outcome.action == "reposted"
    # The new id has been written back so future reconciliations edit
    # in place rather than reposting.
    assert pool.rows["how_to_deposit"]["message_id"] == outcome.message_id


def test_skips_when_no_channel_id_available() -> None:
    """Operator hasn't run /admin setup yet — no fallback, no global_config."""
    pool = _FakePool(rows={}, config={})
    bot = _FakeBot(channels={})

    async def _exercise() -> ReconcileOutcome:
        return await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=None,
            default_title="t",
            default_description="d",
        )

    outcome = asyncio.run(_exercise())
    assert outcome.action == "skipped"
    assert outcome.reason == "channel_id_unknown"


def test_skips_when_channel_lookup_returns_none() -> None:
    """Row exists with channel_id but the bot cannot see that channel
    (no longer in cache, deleted by admin). Skip rather than crash."""
    pool = _FakePool(
        rows={
            "how_to_deposit": {
                "embed_key": "how_to_deposit",
                "channel_id": 999,
                "message_id": None,
                "title": "t",
                "description": "d",
                "color_hex": "#F2B22A",
                "fields": [],
                "image_url": None,
                "footer_text": None,
            }
        }
    )
    bot = _FakeBot(channels={})  # no channel 999

    async def _exercise() -> ReconcileOutcome:
        return await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=None,
            default_title="t",
            default_description="d",
        )

    outcome = asyncio.run(_exercise())
    assert outcome.action == "skipped"
    assert outcome.reason == "channel_not_found"


# ---------------------------------------------------------------------------
# Idempotency: running twice on the same fresh state results in
# (posted, edited) — the AC's classic "restarting twice does not
# duplicate" property.
# ---------------------------------------------------------------------------


def test_running_twice_does_not_duplicate_messages() -> None:
    pool = _FakePool(rows={}, config={})
    channel = _FakeChannel(channel_id=100)
    bot = _FakeBot(channels={100: channel})

    async def _exercise() -> tuple[ReconcileOutcome, ReconcileOutcome]:
        first = await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=100,
            default_title="t",
            default_description="d",
        )
        second = await reconcile_welcome_embed(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
            embed_key="how_to_deposit",
            fallback_channel_id=100,
            default_title="t",
            default_description="d",
        )
        return first, second

    first, second = asyncio.run(_exercise())
    assert first.action == "posted"
    assert second.action == "edited"
    # Only one message was ever sent — second run edited in place.
    assert len(channel.messages) == 1


# ---------------------------------------------------------------------------
# reconcile_welcome_embeds — orchestrator
# ---------------------------------------------------------------------------


def test_orchestrator_processes_every_default() -> None:
    """All canonical defaults run through the reconciler. Channel ids
    come from ``dw.global_config`` keys ``channel_id_<embed_key>``."""
    pool = _FakePool(
        rows={},
        config={
            "channel_id_how_to_deposit": 100,
            "channel_id_how_to_withdraw": 200,
            "channel_id_cashier_onboarding": 300,
        },
    )
    channels = {
        100: _FakeChannel(channel_id=100),
        200: _FakeChannel(channel_id=200),
        300: _FakeChannel(channel_id=300),
    }
    bot = _FakeBot(channels=channels)

    async def _exercise() -> list[ReconcileOutcome]:
        return await reconcile_welcome_embeds(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
        )

    outcomes = asyncio.run(_exercise())
    assert {o.embed_key for o in outcomes} == {
        "how_to_deposit",
        "how_to_withdraw",
        "cashier_onboarding",
    }
    # All three posted on first run.
    assert all(o.action == "posted" for o in outcomes)


def test_orchestrator_skips_unconfigured_channels() -> None:
    """If only one channel id is configured, only that key reconciles;
    the other is skipped with reason channel_id_unknown — no crash."""
    pool = _FakePool(
        rows={},
        config={"channel_id_how_to_deposit": 100},
    )
    channels = {100: _FakeChannel(channel_id=100)}
    bot = _FakeBot(channels=channels)

    async def _exercise() -> list[ReconcileOutcome]:
        return await reconcile_welcome_embeds(
            pool=pool,  # type: ignore[arg-type]
            bot=bot,  # type: ignore[arg-type]
        )

    outcomes = asyncio.run(_exercise())
    by_key = {o.embed_key: o for o in outcomes}
    assert by_key["how_to_deposit"].action == "posted"
    assert by_key["how_to_withdraw"].action == "skipped"
    assert by_key["how_to_withdraw"].reason == "channel_id_unknown"


def test_welcome_default_constants_are_imported_correctly() -> None:
    """Sanity check: each WelcomeDefault has non-empty title and
    description (the seeds inserted on first run)."""
    for w in DEFAULT_WELCOMES:
        assert isinstance(w, WelcomeDefault)
        assert w.title.strip()
        assert w.description.strip()
