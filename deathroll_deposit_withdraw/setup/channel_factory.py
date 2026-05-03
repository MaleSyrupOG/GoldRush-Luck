"""Channel factory for the ``/admin setup`` D/W bootstrap command.

Spec §5.3 says the bot must create two categories (``Banking`` and
``Cashier``) plus eight canonical channels with a specific
permissions matrix. The challenge is that ``/admin setup`` is run
ONCE in a fresh guild and then sometimes re-run after the operator
manually moves things around — so the operation has to be idempotent
and detect existing entities by name + parent.

This module isolates the creation logic so it can be tested without
a real Discord guild. Tests fake the slice of ``discord.py`` we
touch (``Guild.create_category`` /``create_text_channel`` /
``categories`` / ``text_channels`` / ``default_role`` / ``me``).

The module is intentionally **not** aware of the database. Callers
that want to persist channel ids into ``dw.global_config`` pass a
``persist`` async callback; the module hands it a
``{channel_key: discord_id}`` mapping after a successful run.

Reality check: the live server Aleix already runs uses slightly
different names (``#cashier-requests`` instead of ``#cashier-alerts``,
``#audit-log`` and ``#admin-commands`` for admin tooling). Story 3.4
implements the canonical spec naming because the AC is explicit
about it; the operator can rename the Discord channels and re-link
via ``/admin set-channel <key> <existing channel>`` once Story 10.x
lands. This intentional gap is flagged in
``reference_actual_server_state.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategorySpec:
    """A canonical category that ``/admin setup`` provisions.

    ``key`` is the stable identifier the rest of the system uses
    (e.g., to reference the category in ``dw.global_config``).
    ``name`` is the display name in Discord.

    Privacy levels:
    - ``private_to_admin_only=True`` — denies @everyone AND
      @cashier; only @admin (and the bot) sees the category.
      Used for ``Admin`` (audit log, future admin-only tools).
    - ``private_to_staff=True`` (without the admin-only flag)
      — denies @everyone, allows @cashier + @admin. Used for
      ``Cashier`` (alerts, onboarding, disputes).
    - Both False — public read-only-ish category. Used for
      ``Banking``.
    """

    key: str
    name: str
    private_to_staff: bool
    private_to_admin_only: bool = False


@dataclass(frozen=True)
class ChannelSpec:
    """A canonical channel under one of the canonical categories.

    Permission overwrites are computed at creation time from the
    ``key`` (see ``_channel_overwrites``); the matrix is hard-coded
    against spec §5.3 because every channel has a slightly
    different role-permission shape and a generic flag-per-flag
    representation would be more error-prone than a per-channel
    branch.
    """

    key: str
    name: str
    category_key: str


CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec(key="banking", name="Banking", private_to_staff=False),
    CategorySpec(key="cashier", name="Cashier", private_to_staff=True),
    CategorySpec(
        key="admin",
        name="Admin",
        private_to_staff=True,
        private_to_admin_only=True,
    ),
)


CHANNEL_SPECS: tuple[ChannelSpec, ...] = (
    # Banking category — public-facing surfaces.
    ChannelSpec(key="how_to_deposit", name="how-to-deposit", category_key="banking"),
    ChannelSpec(key="how_to_withdraw", name="how-to-withdraw", category_key="banking"),
    ChannelSpec(key="deposit", name="deposit", category_key="banking"),
    ChannelSpec(key="withdraw", name="withdraw", category_key="banking"),
    ChannelSpec(key="online_cashiers", name="online-cashiers", category_key="banking"),
    # Cashier category — staff-only.
    ChannelSpec(key="cashier_alerts", name="cashier-alerts", category_key="cashier"),
    ChannelSpec(key="cashier_onboarding", name="cashier-onboarding", category_key="cashier"),
    ChannelSpec(key="disputes", name="disputes", category_key="cashier"),
    # Admin category — admin-only audit / ops surface.
    ChannelSpec(key="audit_log", name="audit-log", category_key="admin"),
)


# ---------------------------------------------------------------------------
# Outcome / report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryOutcome:
    """Result of provisioning one category.

    ``discord_id`` is ``None`` when ``dry_run=True`` and the category
    didn't already exist (because nothing was created); the rest of
    the system reads this to render the preview embed.
    """

    key: str
    name: str
    discord_id: int | None
    created: bool


@dataclass(frozen=True)
class ChannelOutcome:
    """Result of provisioning one channel."""

    key: str
    name: str
    category_key: str
    discord_id: int | None
    created: bool


@dataclass(frozen=True)
class SetupReport:
    """Aggregate result of one ``/admin setup`` invocation.

    Surfaces ``created_count`` and ``reused_count`` so the preview
    embed can render counts without re-iterating the lists.
    """

    categories: tuple[CategoryOutcome, ...]
    channels: tuple[ChannelOutcome, ...]
    dry_run: bool

    @property
    def created_count(self) -> int:
        return sum(1 for c in self.categories if c.created) + sum(
            1 for c in self.channels if c.created
        )

    @property
    def reused_count(self) -> int:
        return sum(1 for c in self.categories if not c.created) + sum(
            1 for c in self.channels if not c.created
        )


# ---------------------------------------------------------------------------
# Permission matrix — spec §5.3
# ---------------------------------------------------------------------------


def _everyone_overwrite_for_channel(key: str) -> discord.PermissionOverwrite:
    """Return the @everyone permission overwrite for a given channel key.

    Public channels get view + read history; the two interactive
    surfaces (``deposit`` and ``withdraw``) additionally get send +
    use application commands so users can run the slash command.
    Cashier-only and admin-only channels deny view entirely.
    """
    if key in {"how_to_deposit", "how_to_withdraw", "online_cashiers"}:
        return discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
    if key in {"deposit", "withdraw"}:
        return discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            use_application_commands=True,
        )
    # cashier_alerts, cashier_onboarding, disputes, audit_log —
    # staff- or admin-only
    return discord.PermissionOverwrite(view_channel=False)


def _cashier_overwrite_for_channel(key: str) -> discord.PermissionOverwrite | None:
    """Return the @cashier overwrite, or ``None`` to leave inherited.

    The spec puts the cashier role through three distinct postures:
    - On public channels the cashier sees the same as @everyone
      (return None so we do not add an unnecessary overwrite).
    - On the two interactive channels the cashier ALSO needs
      ``view_private_threads`` so they can see the per-ticket private
      thread/channel created off the parent.
    - On cashier-only surfaces the cashier needs view + send.
    - On ``#disputes`` the cashier is denied view (admin-only).
    """
    if key in {"how_to_deposit", "how_to_withdraw", "online_cashiers"}:
        return discord.PermissionOverwrite(view_channel=True, send_messages=False)
    if key in {"deposit", "withdraw"}:
        # The spec calls for "View Private Threads"; Discord exposes this
        # capability through the ``manage_threads`` permission (a member
        # with ``manage_threads`` can see and moderate every thread in
        # the channel, public or private). It is the closest matching
        # real flag — ``view_private_threads`` is not a valid name in
        # discord.py because Discord folded the read side of the bit
        # into ``manage_threads``.
        return discord.PermissionOverwrite(
            view_channel=True,
            manage_threads=True,
            read_message_history=True,
        )
    if key == "cashier_alerts":
        return discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    if key == "cashier_onboarding":
        return discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        )
    if key == "disputes":
        return discord.PermissionOverwrite(view_channel=False)
    if key == "audit_log":
        # Admin-only audit log — cashiers don't get to see admin
        # actions or financial events; that's a deliberate
        # information boundary.
        return discord.PermissionOverwrite(view_channel=False)
    return None  # safety; should be unreachable for canonical keys


def _admin_overwrite_for_channel(key: str) -> discord.PermissionOverwrite:
    """@admin sees + sends in every D/W channel by spec.

    ``key`` is accepted for symmetry; we keep the per-key signature
    so future spec evolutions (e.g., admin gets manage_messages on
    one specific channel) can be folded in without changing the
    factory's plumbing.
    """
    _ = key
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        use_application_commands=True,
    )


def _bot_overwrite_for_channel(key: str) -> discord.PermissionOverwrite:
    """The bot needs send + read everywhere, plus channel-specific extras.

    Per spec §5.3:
    - ``#how-to-deposit`` / ``#how-to-withdraw`` / ``#online-cashiers``
      get ``manage_messages`` so the bot can edit pinned welcome
      embeds in place.
    - ``#deposit`` gets ``manage_threads`` and ``manage_channels``
      because the ticket private channels are created off it (the
      live server uses private channels rather than threads, but the
      permission set covers both flows).
    - ``#withdraw`` gets ``manage_threads``.
    - ``#cashier-alerts`` gets ``send_messages`` (already in the
      base set, listed for clarity).
    """
    base: dict[str, bool] = dict(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        use_application_commands=True,
        embed_links=True,
        attach_files=True,
        manage_messages=False,
        manage_threads=False,
        manage_channels=False,
    )
    if key in {"how_to_deposit", "how_to_withdraw", "online_cashiers"}:
        base["manage_messages"] = True
    if key == "deposit":
        base["manage_threads"] = True
        base["manage_channels"] = True
    if key == "withdraw":
        base["manage_threads"] = True
    return discord.PermissionOverwrite(**base)


def _category_overwrites(
    spec: CategorySpec,
    *,
    everyone: discord.Role,
    cashier: discord.Role | None,
    admin: discord.Role | None,
    bot: discord.Member,
) -> dict[Any, discord.PermissionOverwrite]:
    """Build the overwrites dict for a category.

    The bot ALWAYS receives an explicit ``view_channel=True`` +
    ``manage_channels=True`` overwrite. Without it, when a category
    is created with ``@everyone view_channel=False`` (the
    ``private_to_staff`` case for ``Cashier``), Discord propagates
    that deny to the bot too — even though the bot has Manage
    Channels at the server level — and the subsequent
    ``create_text_channel`` call under that category fails with
    ``403 Missing Permissions``. The explicit bot overwrite breaks
    the inheritance chain.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {}
    if spec.private_to_admin_only:
        # Admin-only: deny @everyone AND @cashier; only @admin sees.
        overwrites[everyone] = discord.PermissionOverwrite(view_channel=False)
        if cashier is not None:
            overwrites[cashier] = discord.PermissionOverwrite(view_channel=False)
    elif spec.private_to_staff:
        overwrites[everyone] = discord.PermissionOverwrite(view_channel=False)
        if cashier is not None:
            overwrites[cashier] = discord.PermissionOverwrite(view_channel=True)
    else:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True
        )
    if admin is not None:
        overwrites[admin] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
    overwrites[bot] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        manage_channels=True,
        manage_messages=True,
        manage_threads=True,
        read_message_history=True,
    )
    return overwrites


def _channel_overwrites(
    key: str,
    *,
    everyone: discord.Role,
    cashier: discord.Role | None,
    admin: discord.Role | None,
    bot: discord.Member,
) -> dict[Any, discord.PermissionOverwrite]:
    """Build the overwrites dict for a single channel.

    Per-channel permissions override per-category permissions in
    Discord, so we always set the @everyone overwrite even on
    cashier-only channels — the explicit deny is more reliable than
    relying on category inheritance.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        everyone: _everyone_overwrite_for_channel(key),
    }
    if cashier is not None:
        cashier_ow = _cashier_overwrite_for_channel(key)
        if cashier_ow is not None:
            overwrites[cashier] = cashier_ow
    if admin is not None:
        overwrites[admin] = _admin_overwrite_for_channel(key)
    overwrites[bot] = _bot_overwrite_for_channel(key)
    return overwrites


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# Caller-supplied async function that writes the channel id map into
# ``dw.global_config``. Kept abstract so this module does not depend
# on a DB connection.
ConfigPersister = Callable[[dict[str, int]], Awaitable[None]]


_REASON = "DeathRoll /admin setup"


async def setup_or_reuse_channels(
    guild: discord.Guild,
    *,
    cashier_role_id: int | None = None,
    admin_role_id: int | None = None,
    dry_run: bool = False,
    persist: ConfigPersister | None = None,
) -> SetupReport:
    """Provision (or reuse) the canonical D/W categories and channels.

    Parameters:
        guild: the guild to set up.
        cashier_role_id: the @cashier role's snowflake id; if ``None``
            the cashier-specific overwrites are skipped (callable still
            succeeds — useful when the operator has not yet created
            the role).
        admin_role_id: the @admin role's snowflake id; same treatment
            as cashier.
        dry_run: when ``True``, no Discord-side mutation happens. The
            returned report still distinguishes existing entities
            (``created=False``) from would-be-created ones
            (``created=True``, ``discord_id=None``).
        persist: optional async callback that receives a mapping
            ``{channel_key: discord_id}`` after a successful real run.
            Use this to persist channel ids into ``dw.global_config``.
            Skipped on ``dry_run``.

    Returns:
        ``SetupReport`` describing every category and channel — newly
        created and reused alike — for use in the ``/admin setup``
        confirmation embed.
    """
    cashier_role = guild.get_role(cashier_role_id) if cashier_role_id is not None else None
    admin_role = guild.get_role(admin_role_id) if admin_role_id is not None else None
    bot_member = guild.me
    everyone_role = guild.default_role

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------
    cat_outcomes: list[CategoryOutcome] = []
    cat_by_key: dict[str, discord.CategoryChannel | None] = {}

    for spec in CATEGORY_SPECS:
        existing = next((c for c in guild.categories if c.name == spec.name), None)
        if existing is not None:
            cat_outcomes.append(
                CategoryOutcome(
                    key=spec.key,
                    name=spec.name,
                    discord_id=existing.id,
                    created=False,
                )
            )
            cat_by_key[spec.key] = existing
            continue

        if dry_run:
            cat_outcomes.append(
                CategoryOutcome(
                    key=spec.key,
                    name=spec.name,
                    discord_id=None,
                    created=True,
                )
            )
            cat_by_key[spec.key] = None
            continue

        overwrites = _category_overwrites(
            spec,
            everyone=everyone_role,
            cashier=cashier_role,
            admin=admin_role,
            bot=bot_member,
        )
        created = await guild.create_category(
            spec.name, overwrites=overwrites, reason=_REASON
        )
        cat_outcomes.append(
            CategoryOutcome(
                key=spec.key,
                name=spec.name,
                discord_id=created.id,
                created=True,
            )
        )
        cat_by_key[spec.key] = created

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    cat_name_by_key = {s.key: s.name for s in CATEGORY_SPECS}
    ch_outcomes: list[ChannelOutcome] = []

    for ch_spec in CHANNEL_SPECS:
        parent_name = cat_name_by_key[ch_spec.category_key]
        existing_ch = next(
            (
                c
                for c in guild.text_channels
                if c.name == ch_spec.name
                and c.category is not None
                and c.category.name == parent_name
            ),
            None,
        )
        if existing_ch is not None:
            ch_outcomes.append(
                ChannelOutcome(
                    key=ch_spec.key,
                    name=ch_spec.name,
                    category_key=ch_spec.category_key,
                    discord_id=existing_ch.id,
                    created=False,
                )
            )
            continue

        if dry_run:
            ch_outcomes.append(
                ChannelOutcome(
                    key=ch_spec.key,
                    name=ch_spec.name,
                    category_key=ch_spec.category_key,
                    discord_id=None,
                    created=True,
                )
            )
            continue

        overwrites = _channel_overwrites(
            ch_spec.key,
            everyone=everyone_role,
            cashier=cashier_role,
            admin=admin_role,
            bot=bot_member,
        )
        parent = cat_by_key[ch_spec.category_key]
        created_ch = await guild.create_text_channel(
            ch_spec.name,
            category=parent,
            overwrites=overwrites,
            reason=_REASON,
        )
        ch_outcomes.append(
            ChannelOutcome(
                key=ch_spec.key,
                name=ch_spec.name,
                category_key=ch_spec.category_key,
                discord_id=created_ch.id,
                created=True,
            )
        )

    report = SetupReport(
        categories=tuple(cat_outcomes),
        channels=tuple(ch_outcomes),
        dry_run=dry_run,
    )

    # Persist channel ids into dw.global_config (only on real run).
    if persist is not None and not dry_run:
        id_map = {
            c.key: c.discord_id
            for c in ch_outcomes
            if c.discord_id is not None
        }
        await persist(id_map)

    return report
