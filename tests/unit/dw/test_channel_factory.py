"""Unit tests for `goldrush_deposit_withdraw.setup.channel_factory`.

The factory provisions the canonical D/W category + channel layout
(per spec §5.3) idempotently. It must:

- Create every missing category and channel on a fresh guild.
- Reuse anything already present (matched by name + parent).
- Apply the spec §5.3 permission matrix exactly.
- Be observable via a typed ``SetupReport`` so the ``/admin setup``
  command can render a preview / confirmation embed.
- Support ``dry_run=True`` so admins can inspect the plan before any
  Discord-side mutation happens.
- Optionally persist every channel id to ``dw.global_config`` via a
  caller-provided async callback (the DB layer is intentionally
  outside this module's concern).

Tests run against in-process fakes that mimic the slice of
``discord.py`` we use; no network or Discord client is required.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import discord
import pytest
from goldrush_deposit_withdraw.setup.channel_factory import (
    CATEGORY_SPECS,
    CHANNEL_SPECS,
    CategoryOutcome,
    CategorySpec,
    ChannelOutcome,
    ChannelSpec,
    SetupReport,
    setup_or_reuse_channels,
)

# ---------------------------------------------------------------------------
# Discord-API fakes
# ---------------------------------------------------------------------------


class _FakeRole:
    """Cheap stand-in for ``discord.Role`` — only ``id`` is used."""

    def __init__(self, role_id: int, name: str = "") -> None:
        self.id = role_id
        self.name = name

    def __hash__(self) -> int:
        return hash(("role", self.id))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeRole) and other.id == self.id


class _FakeMember:
    """Cheap stand-in for ``discord.Member`` — used to represent the bot user."""

    def __init__(self, user_id: int) -> None:
        self.id = user_id

    def __hash__(self) -> int:
        return hash(("member", self.id))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeMember) and other.id == self.id


@dataclass
class _FakeCategory:
    id: int
    name: str
    overwrites: dict[Any, discord.PermissionOverwrite] = field(default_factory=dict)


@dataclass
class _FakeChannel:
    id: int
    name: str
    category: _FakeCategory | None = None
    overwrites: dict[Any, discord.PermissionOverwrite] = field(default_factory=dict)


class _FakeGuild:
    """Minimal subset of ``discord.Guild`` used by the factory.

    Records every API call in ``self.calls`` so tests can assert the
    factory only mutated what it was supposed to.
    """

    def __init__(
        self,
        *,
        cashier_role: _FakeRole | None = None,
        admin_role: _FakeRole | None = None,
        bot_member: _FakeMember | None = None,
    ) -> None:
        self.id = 1
        self.default_role = _FakeRole(role_id=0, name="@everyone")
        self._cashier_role = cashier_role
        self._admin_role = admin_role
        self._me = bot_member or _FakeMember(user_id=999)
        self._categories: list[_FakeCategory] = []
        self._channels: list[_FakeChannel] = []
        self._next_id = 100
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # API surface used by the factory
    @property
    def categories(self) -> list[_FakeCategory]:
        return list(self._categories)

    @property
    def text_channels(self) -> list[_FakeChannel]:
        return list(self._channels)

    @property
    def me(self) -> _FakeMember:
        return self._me

    def get_role(self, role_id: int) -> _FakeRole | None:
        if self._cashier_role and self._cashier_role.id == role_id:
            return self._cashier_role
        if self._admin_role and self._admin_role.id == role_id:
            return self._admin_role
        return None

    async def create_category(
        self,
        name: str,
        *,
        overwrites: dict[Any, discord.PermissionOverwrite] | None = None,
        reason: str | None = None,
    ) -> _FakeCategory:
        cat = _FakeCategory(id=self._next_id, name=name, overwrites=overwrites or {})
        self._next_id += 1
        self._categories.append(cat)
        self.calls.append(("create_category", {"name": name, "overwrites": overwrites or {}}))
        _ = reason
        return cat

    async def create_text_channel(
        self,
        name: str,
        *,
        category: _FakeCategory | None = None,
        overwrites: dict[Any, discord.PermissionOverwrite] | None = None,
        reason: str | None = None,
    ) -> _FakeChannel:
        ch = _FakeChannel(
            id=self._next_id,
            name=name,
            category=category,
            overwrites=overwrites or {},
        )
        self._next_id += 1
        self._channels.append(ch)
        self.calls.append(
            (
                "create_text_channel",
                {
                    "name": name,
                    "category": category,
                    "overwrites": overwrites or {},
                },
            )
        )
        _ = reason
        return ch

    # Test helpers — preload an existing category or channel so we can
    # exercise the "reuse" branch.
    def _preload_category(self, name: str) -> _FakeCategory:
        cat = _FakeCategory(id=self._next_id, name=name)
        self._next_id += 1
        self._categories.append(cat)
        return cat

    def _preload_channel(self, name: str, category: _FakeCategory) -> _FakeChannel:
        ch = _FakeChannel(id=self._next_id, name=name, category=category)
        self._next_id += 1
        self._channels.append(ch)
        return ch


def _build_guild() -> tuple[_FakeGuild, _FakeRole, _FakeRole]:
    cashier = _FakeRole(role_id=10, name="cashier")
    admin = _FakeRole(role_id=20, name="admin")
    guild = _FakeGuild(cashier_role=cashier, admin_role=admin)
    return guild, cashier, admin


# ---------------------------------------------------------------------------
# Spec sanity tests
# ---------------------------------------------------------------------------


def test_two_canonical_categories_banking_and_cashier() -> None:
    keys = {c.key for c in CATEGORY_SPECS}
    assert keys == {"banking", "cashier"}


def test_eight_canonical_channels_per_spec() -> None:
    """Spec §5.3 lists exactly 8 channels: 5 in Banking, 3 in Cashier."""
    keys = {c.key for c in CHANNEL_SPECS}
    expected = {
        "how_to_deposit",
        "how_to_withdraw",
        "deposit",
        "withdraw",
        "online_cashiers",
        "cashier_alerts",
        "cashier_onboarding",
        "disputes",
    }
    assert keys == expected


def test_each_channel_spec_belongs_to_known_category() -> None:
    cat_keys = {c.key for c in CATEGORY_SPECS}
    for ch in CHANNEL_SPECS:
        assert ch.category_key in cat_keys, f"channel {ch.key} has unknown category"


def test_channel_spec_names_are_lowercase_dash_separated() -> None:
    """Discord channel names must be lowercase; we use dashes per convention."""
    for ch in CHANNEL_SPECS:
        assert ch.name == ch.name.lower()
        assert " " not in ch.name
        assert "_" not in ch.name


# ---------------------------------------------------------------------------
# Fresh-guild flow — every category and channel is created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_on_fresh_guild_creates_two_categories_and_eight_channels() -> None:
    guild, cashier, admin = _build_guild()
    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )

    # 2 + 8 = 10 entities created; nothing reused.
    assert len(guild.categories) == 2
    assert len(guild.text_channels) == 8
    assert sum(1 for c in report.categories if c.created) == 2
    assert sum(1 for c in report.channels if c.created) == 8
    assert report.created_count == 10
    assert report.reused_count == 0


@pytest.mark.asyncio
async def test_setup_on_fresh_guild_reports_correct_outcome_keys() -> None:
    guild, cashier, admin = _build_guild()
    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    cat_keys = [c.key for c in report.categories]
    assert sorted(cat_keys) == sorted(["banking", "cashier"])
    ch_keys = sorted(c.key for c in report.channels)
    assert ch_keys == sorted(s.key for s in CHANNEL_SPECS)


@pytest.mark.asyncio
async def test_setup_associates_each_channel_with_correct_category() -> None:
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    cats_by_name = {c.name: c for c in guild.categories}
    for ch in guild.text_channels:
        spec = next(s for s in CHANNEL_SPECS if s.name == ch.name)
        cat_spec = next(c for c in CATEGORY_SPECS if c.key == spec.category_key)
        assert ch.category is not None
        assert ch.category.name == cat_spec.name, (
            f"channel {ch.name} should be in {cat_spec.name}, got "
            f"{ch.category.name if ch.category else 'None'}"
        )
        assert cats_by_name[cat_spec.name] is ch.category


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_run_creates_nothing_when_state_unchanged() -> None:
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    cat_count_after_first = len(guild.categories)
    ch_count_after_first = len(guild.text_channels)
    calls_before_second = len(guild.calls)

    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )

    assert len(guild.categories) == cat_count_after_first
    assert len(guild.text_channels) == ch_count_after_first
    # No additional API calls beyond the first run's calls.
    assert len(guild.calls) == calls_before_second
    assert report.created_count == 0
    assert report.reused_count == 10


@pytest.mark.asyncio
async def test_partial_state_creates_only_missing_channels() -> None:
    """Operator manually created the Banking category but no channels yet.

    Setup should reuse the existing category and create only the
    missing channels under it, plus the second category, plus its
    channels.
    """
    guild, cashier, admin = _build_guild()
    banking = guild._preload_category("Banking")
    guild._preload_channel("how-to-deposit", banking)
    guild._preload_channel("deposit", banking)

    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )

    assert len(guild.categories) == 2  # banking reused, cashier created
    assert len(guild.text_channels) == 8  # 2 reused, 6 created
    assert report.reused_count == 1 + 2  # 1 category + 2 channels
    assert report.created_count == 1 + 6  # 1 category + 6 channels


# ---------------------------------------------------------------------------
# dry_run — nothing mutates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_call_create_apis() -> None:
    guild, cashier, admin = _build_guild()
    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
        dry_run=True,
    )

    assert guild.calls == []
    assert guild.categories == []
    assert guild.text_channels == []
    assert report.dry_run is True
    # The report still shows what WOULD be created so /admin setup --dry-run
    # is a real preview.
    assert report.created_count == 10
    assert report.reused_count == 0
    # IDs are None in dry-run because nothing was created.
    for c in report.categories:
        assert c.discord_id is None
    for c in report.channels:
        assert c.discord_id is None


@pytest.mark.asyncio
async def test_dry_run_marks_existing_entities_as_reused() -> None:
    """Dry-run on a partially-set-up guild reports the truth, not a plan."""
    guild, cashier, admin = _build_guild()
    banking = guild._preload_category("Banking")
    guild._preload_channel("how-to-deposit", banking)

    report = await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
        dry_run=True,
    )

    assert report.reused_count == 2  # Banking + how-to-deposit
    assert report.created_count == 8  # 1 category + 7 channels


# ---------------------------------------------------------------------------
# Persistence callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_callback_invoked_with_full_channel_id_map() -> None:
    """After non-dry-run, the persist callback receives every channel id."""
    guild, cashier, admin = _build_guild()
    received: list[dict[str, int]] = []

    async def persist(channel_id_map: dict[str, int]) -> None:
        received.append(dict(channel_id_map))

    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
        persist=persist,
    )

    assert len(received) == 1
    payload = received[0]
    expected_keys = {s.key for s in CHANNEL_SPECS}
    assert set(payload.keys()) == expected_keys
    # Every value is a real (positive) integer id.
    assert all(isinstance(v, int) and v > 0 for v in payload.values())


@pytest.mark.asyncio
async def test_persist_callback_not_called_on_dry_run() -> None:
    guild, cashier, admin = _build_guild()
    called = False

    async def persist(channel_id_map: dict[str, int]) -> None:
        nonlocal called
        called = True

    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
        persist=persist,
        dry_run=True,
    )
    assert called is False


# ---------------------------------------------------------------------------
# Permission overwrites — spec §5.3 matrix
# ---------------------------------------------------------------------------


def _channel_by_name(guild: _FakeGuild, name: str) -> _FakeChannel:
    for ch in guild.text_channels:
        if ch.name == name:
            return ch
    raise AssertionError(f"channel {name!r} not present in fake guild")


@pytest.mark.asyncio
async def test_cashier_category_denies_everyone_view() -> None:
    """The Cashier category is private to staff."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    cat = next(c for c in guild.categories if c.name == "Cashier")
    everyone_overwrite = cat.overwrites[guild.default_role]
    assert everyone_overwrite.view_channel is False


@pytest.mark.asyncio
async def test_disputes_channel_hides_from_everyone_and_cashier() -> None:
    """Spec §5.3: disputes channel is admin-only."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    ch = _channel_by_name(guild, "disputes")
    assert ch.overwrites[guild.default_role].view_channel is False
    assert ch.overwrites[cashier].view_channel is False
    assert ch.overwrites[admin].view_channel is True


@pytest.mark.asyncio
async def test_cashier_alerts_denies_everyone_but_allows_cashier_send() -> None:
    """Spec §5.3: cashier-alerts denies @everyone view, allows cashier view+send."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    ch = _channel_by_name(guild, "cashier-alerts")
    assert ch.overwrites[guild.default_role].view_channel is False
    assert ch.overwrites[cashier].view_channel is True
    assert ch.overwrites[cashier].send_messages is True


@pytest.mark.asyncio
async def test_how_to_deposit_is_public_read_only() -> None:
    """Spec §5.3: how-to-deposit allows everyone view + read history, no send."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    ch = _channel_by_name(guild, "how-to-deposit")
    everyone = ch.overwrites[guild.default_role]
    assert everyone.view_channel is True
    assert everyone.read_message_history is True
    assert everyone.send_messages is False


@pytest.mark.asyncio
async def test_deposit_channel_allows_everyone_send_and_use_app_commands() -> None:
    """Spec §5.3: #deposit allows view + send + use app cmds (this is where /deposit fires)."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    ch = _channel_by_name(guild, "deposit")
    everyone = ch.overwrites[guild.default_role]
    assert everyone.view_channel is True
    assert everyone.send_messages is True
    assert everyone.use_application_commands is True


@pytest.mark.asyncio
async def test_bot_has_manage_channels_on_deposit_for_thread_creation() -> None:
    """Per spec §5.3 the bot needs Manage Channels on #deposit (creates ticket channels)."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    ch = _channel_by_name(guild, "deposit")
    bot = ch.overwrites[guild.me]
    assert bot.manage_channels is True
    assert bot.manage_threads is True


@pytest.mark.asyncio
async def test_admin_role_always_sees_every_channel() -> None:
    """@admin must view every D/W channel, including disputes."""
    guild, cashier, admin = _build_guild()
    await setup_or_reuse_channels(
        guild,  # type: ignore[arg-type]
        cashier_role_id=cashier.id,
        admin_role_id=admin.id,
    )
    for ch in guild.text_channels:
        admin_ow = ch.overwrites.get(admin)
        assert admin_ow is not None, f"channel {ch.name} missing @admin overwrite"
        assert admin_ow.view_channel is True


# ---------------------------------------------------------------------------
# Optional roles — gracefully degrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_works_without_role_ids_provided() -> None:
    """If the operator has not yet created @cashier / @admin roles,
    the factory still creates the layout. Cashier-only channels deny
    everyone; admin overwrites are simply not applied.

    This is a recoverable state — the operator runs ``/admin setup``
    again after creating the roles, and the role-based overwrites
    are then layered onto the existing channels.
    """
    guild = _FakeGuild()  # no roles set up
    report = await setup_or_reuse_channels(guild)  # type: ignore[arg-type]
    assert report.created_count == 10
    # The cashier-only channels still exist and deny @everyone view.
    cashier_alerts = _channel_by_name(guild, "cashier-alerts")
    assert cashier_alerts.overwrites[guild.default_role].view_channel is False


# ---------------------------------------------------------------------------
# SetupReport sanity
# ---------------------------------------------------------------------------


def test_setup_report_dataclass_is_iterable_and_typed() -> None:
    """The report must be cleanly constructible from typed outcomes."""
    cat = CategoryOutcome(key="banking", name="Banking", discord_id=1, created=True)
    ch = ChannelOutcome(
        key="deposit",
        name="deposit",
        category_key="banking",
        discord_id=2,
        created=True,
    )
    r = SetupReport(categories=(cat,), channels=(ch,), dry_run=False)
    assert r.created_count == 2
    assert r.reused_count == 0
    assert r.dry_run is False


def test_category_and_channel_specs_are_immutable() -> None:
    """``frozen=True`` on the dataclasses guards against accidental mutation."""
    spec = CATEGORY_SPECS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "NotAllowed"  # type: ignore[misc]
    cspec = CHANNEL_SPECS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cspec.name = "no"  # type: ignore[misc]
    assert isinstance(CATEGORY_SPECS[0], CategorySpec)
    assert isinstance(CHANNEL_SPECS[0], ChannelSpec)
