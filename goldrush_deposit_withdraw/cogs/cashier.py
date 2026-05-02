"""Cashier cog — every ``/cashier *`` command (Epic 7).

Stories 7.1, 7.2, 7.3 land here:

- ``/cashier addchar`` / ``/cashier removechar`` / ``/cashier listchars``
  — character roster self-service. Restricted to the
  ``#cashier-onboarding`` channel.
- ``/cashier set-status`` — toggle online / offline / break.
  Available in any channel; updates ``dw.cashier_status`` and
  drives the bookkeeping in ``dw.cashier_sessions``.
- ``/cashier mystats`` — ephemeral self-service stats from
  ``dw.cashier_stats``.

The slash command tree handles the role-restriction at the
Discord-side via ``default_permissions`` once Story 10.4 lands;
until then admins should set the visibility manually in
*Server Settings → Integrations*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
import structlog
from discord import app_commands
from discord.ext import commands
from goldrush_core.balance import exceptions as exc
from goldrush_core.balance.dw_manager import (
    add_cashier_character,
    remove_cashier_character,
    set_cashier_status,
)
from goldrush_core.discord_helpers.channel_binding import resolve_channel_id
from goldrush_core.embeds.dw_tickets import cashier_stats_embed

if TYPE_CHECKING:
    from goldrush_deposit_withdraw.client import DwBot


_log = structlog.get_logger(__name__)


# Discord choice menus give the user a dropdown rather than free text,
# preventing typos and matching the Literal types the SECURITY DEFINER
# fns enforce. Mismatched values would still surface as InvalidRegion /
# InvalidFaction at the SQL layer; the choices are just better UX.
_REGION_CHOICES = [
    app_commands.Choice(name="EU", value="EU"),
    app_commands.Choice(name="NA", value="NA"),
]
_FACTION_CHOICES = [
    app_commands.Choice(name="Alliance", value="Alliance"),
    app_commands.Choice(name="Horde", value="Horde"),
]
_STATUS_CHOICES = [
    app_commands.Choice(name="online", value="online"),
    app_commands.Choice(name="offline", value="offline"),
    app_commands.Choice(name="break", value="break"),
]


class CashierCog(commands.Cog):
    """Hosts every ``/cashier *`` slash command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # Story 7.1: addchar / removechar / listchars
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="cashier-addchar",
        description="Register a character to your cashier roster.",
    )
    @app_commands.describe(
        char="In-game character name",
        realm="In-game realm (e.g., Stormrage)",
        region="Region — EU or NA",
        faction="Faction — Alliance or Horde",
    )
    @app_commands.choices(region=_REGION_CHOICES, faction=_FACTION_CHOICES)
    async def addchar(
        self,
        interaction: discord.Interaction,
        char: str,
        realm: str,
        region: app_commands.Choice[str],
        faction: app_commands.Choice[str],
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if not await _verify_in_cashier_onboarding(interaction, bot):
            return
        try:
            row_id = await add_cashier_character(
                bot.pool,  # type: ignore[arg-type]
                discord_id=interaction.user.id,
                char=char,
                realm=realm,
                region=region.value,  # type: ignore[arg-type]
                faction=faction.value,  # type: ignore[arg-type]
            )
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not register character: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Registered **{char}** ({realm}, {region.value} {faction.value}) "
            f"to your roster (id #{row_id}).",
            ephemeral=True,
        )
        _log.info(
            "cashier_addchar",
            cashier_id=interaction.user.id,
            char=char,
            realm=realm,
            region=region.value,
            faction=faction.value,
            row_id=row_id,
        )

    @app_commands.command(
        name="cashier-removechar",
        description="Soft-remove a character from your cashier roster.",
    )
    @app_commands.describe(
        char="In-game character name",
        realm="In-game realm",
        region="Region — EU or NA",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    async def removechar(
        self,
        interaction: discord.Interaction,
        char: str,
        realm: str,
        region: app_commands.Choice[str],
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if not await _verify_in_cashier_onboarding(interaction, bot):
            return
        try:
            await remove_cashier_character(
                bot.pool,  # type: ignore[arg-type]
                discord_id=interaction.user.id,
                char=char,
                realm=realm,
                region=region.value,  # type: ignore[arg-type]
            )
        except exc.CharacterNotFoundOrAlreadyRemoved:
            await interaction.response.send_message(
                f"❌ No active character matched **{char}** on {realm} ({region.value}).",
                ephemeral=True,
            )
            return
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not remove character: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Removed **{char}** ({realm}, {region.value}) from your roster.",
            ephemeral=True,
        )
        _log.info(
            "cashier_removechar",
            cashier_id=interaction.user.id,
            char=char,
            realm=realm,
            region=region.value,
        )

    @app_commands.command(
        name="cashier-listchars",
        description="List your active cashier characters.",
    )
    async def listchars(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if not await _verify_in_cashier_onboarding(interaction, bot):
            return
        rows = await bot.pool.fetch(  # type: ignore[union-attr]
            """
            SELECT char_name, realm, region, faction, added_at
              FROM dw.cashier_characters
              WHERE discord_id = $1
                AND is_active = TRUE
              ORDER BY region, faction, char_name
            """,
            interaction.user.id,
        )
        if not rows:
            await interaction.response.send_message(
                "You have no active cashier characters. Use `/cashier-addchar` "
                "to register one.",
                ephemeral=True,
            )
            return
        lines = [
            f"• **{r['char_name']}** — {r['realm']} ({r['region']} {r['faction']})"
            for r in rows
        ]
        embed = discord.Embed(
            title="Your active characters",
            description="\n".join(lines),
            color=discord.Color(0x5DBE5A),
        )
        embed.set_footer(text=f"{len(rows)} active character(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # Story 7.2: set-status
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="cashier-set-status",
        description="Set your cashier status — online / offline / break.",
    )
    @app_commands.describe(status="online, offline or break")
    @app_commands.choices(status=_STATUS_CHOICES)
    async def set_status(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
    ) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        try:
            await set_cashier_status(
                bot.pool,
                discord_id=interaction.user.id,
                status=status.value,  # type: ignore[arg-type]
            )
        except exc.BalanceError as e:
            await interaction.response.send_message(
                f"❌ Could not set status: {e.message}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Status set to **{status.value}**. The online-cashiers embed "
            f"updates within 30 seconds.",
            ephemeral=True,
        )
        _log.info(
            "cashier_set_status",
            cashier_id=interaction.user.id,
            status=status.value,
        )

    # -----------------------------------------------------------------------
    # Story 7.3: mystats
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="cashier-mystats",
        description="Show your cashier stats (ephemeral).",
    )
    async def mystats(self, interaction: discord.Interaction) -> None:
        bot: DwBot = self.bot  # type: ignore[assignment]
        if bot.pool is None:
            await interaction.response.send_message(
                "Bot is still starting up — try again in a few seconds.",
                ephemeral=True,
            )
            return
        row = await bot.pool.fetchrow(
            """
            SELECT deposits_completed, deposits_cancelled,
                   withdraws_completed, withdraws_cancelled,
                   total_volume_g, total_online_seconds,
                   avg_claim_to_confirm_s, last_active_at
              FROM dw.cashier_stats
              WHERE discord_id = $1
            """,
            interaction.user.id,
        )
        embed = _stats_embed(
            cashier_mention=interaction.user.mention,
            row=row,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_in_cashier_onboarding(
    interaction: discord.Interaction, bot: DwBot
) -> bool:
    """Return True if the interaction is in ``#cashier-onboarding``.

    Sends an ephemeral redirect on mismatch; the caller returns
    False to short-circuit the command body.
    """
    if bot.pool is None:
        await interaction.response.send_message(
            "Bot is still starting up — try again in a few seconds.",
            ephemeral=True,
        )
        return False
    configured = await resolve_channel_id(bot.pool, "cashier_onboarding")
    if configured is None:
        await interaction.response.send_message(
            "Cashier-onboarding channel not configured yet — ask an admin to "
            "run `/admin setup`.",
            ephemeral=True,
        )
        return False
    if interaction.channel_id != configured:
        await interaction.response.send_message(
            f"Use this command in <#{configured}>.",
            ephemeral=True,
        )
        return False
    return True


def _stats_embed(*, cashier_mention: str, row: object) -> discord.Embed:
    """Render the cashier stats embed.

    A new cashier with no row yet sees a zero-filled stats card —
    spec §6.3 example shows this is the right experience for first-
    time use.
    """
    if row is None:
        return cashier_stats_embed(
            cashier_mention=cashier_mention,
            deposits_completed=0,
            deposits_cancelled=0,
            withdraws_completed=0,
            withdraws_cancelled=0,
            total_volume_g=0,
            total_online_seconds=0,
            avg_claim_to_confirm_s=None,
            last_active_at=None,
        )
    # asyncpg.Record exposes ``__getitem__``; we use a permissive
    # ``object`` annotation here because the cog's tests substitute a
    # plain dict.
    return cashier_stats_embed(
        cashier_mention=cashier_mention,
        deposits_completed=int(row["deposits_completed"]),  # type: ignore[index]
        deposits_cancelled=int(row["deposits_cancelled"]),  # type: ignore[index]
        withdraws_completed=int(row["withdraws_completed"]),  # type: ignore[index]
        withdraws_cancelled=int(row["withdraws_cancelled"]),  # type: ignore[index]
        total_volume_g=int(row["total_volume_g"]),  # type: ignore[index]
        total_online_seconds=int(row["total_online_seconds"]),  # type: ignore[index]
        avg_claim_to_confirm_s=(
            int(row["avg_claim_to_confirm_s"])  # type: ignore[index]
            if row["avg_claim_to_confirm_s"] is not None  # type: ignore[index]
            else None
        ),
        last_active_at=row["last_active_at"],  # type: ignore[index]
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CashierCog(bot))


# Mark Literal as referenced — used in inline type hints below for
# clarity even though the cog's signatures use Choice[str].
_ = Literal
