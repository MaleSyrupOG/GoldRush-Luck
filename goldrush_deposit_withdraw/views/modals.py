"""Discord modals shared between the D/W cogs.

The 2FA confirmation modal is the most security-critical surface
in the bot: it is the human friction layer that gates every gold
movement (cashier ``/confirm``, admin treasury ops). The magic
word is typed VERBATIM — the validator is intentionally
case-sensitive so a sloppy "confirm" cannot be mistaken for an
intentional confirmation.

The deposit / withdraw input modals (``DepositModal`` /
``WithdrawModal``) are also defined here. They wrap pydantic
validators (``DepositModalInput`` / ``WithdrawModalInput``) so the
input format rules live in one place.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from goldrush_core.models.dw_pydantic import (
    DepositModalInput,
    EditDynamicEmbedInput,
    WithdrawModalInput,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Magic-word matcher (testable apart from Discord)
# ---------------------------------------------------------------------------


def is_magic_word_match(*, supplied: str, expected: str) -> bool:
    """Return True iff ``supplied`` (after stripping whitespace) equals ``expected`` exactly.

    Whitespace stripping forgives accidental spaces / newlines a
    Discord text input may produce on copy-paste; case sensitivity
    is preserved so a lowercase "confirm" never accidentally arms
    a deposit confirmation.
    """
    return supplied.strip() == expected


# ---------------------------------------------------------------------------
# Confirmation modal — used by /confirm and treasury ops
# ---------------------------------------------------------------------------


# Type alias for the post-confirmation callback. The cog supplies
# an async function that runs the actual SECURITY DEFINER call once
# the magic word is verified.
_ConfirmCallback = Callable[[discord.Interaction], Awaitable[None]]


class ConfirmTicketModal(discord.ui.Modal, title="Confirm action"):
    """Two-factor confirmation: type the exact magic word.

    Constructed per-invocation with the ``magic_word`` and the
    ``on_confirm`` callback. Mismatch → ephemeral cancel; match →
    delegate to the callback (which must call
    ``interaction.response.send_message`` itself, since the modal
    submission consumes the deferred response token).
    """

    confirmation: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Confirm",
        placeholder="Type the magic word exactly",
        required=True,
        max_length=64,
    )

    def __init__(self, *, magic_word: str, on_confirm: _ConfirmCallback) -> None:
        super().__init__()
        self._magic_word = magic_word
        self._on_confirm = on_confirm
        # Update the placeholder so the user sees what to type.
        self.confirmation.placeholder = f"Type {magic_word} to confirm"
        self.confirmation.label = f"Type {magic_word}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not is_magic_word_match(
            supplied=self.confirmation.value, expected=self._magic_word
        ):
            await interaction.response.send_message(
                f"Confirmation cancelled — expected to read `{self._magic_word}`.",
                ephemeral=True,
            )
            return
        await self._on_confirm(interaction)


# ---------------------------------------------------------------------------
# Deposit / Withdraw input modals
# ---------------------------------------------------------------------------


class _TicketInputModal(discord.ui.Modal):
    """Common scaffold for deposit / withdraw input modals.

    Five text inputs matching the spec §5.5 fields. The submit
    handler routes the raw values through the pydantic validator
    appropriate for the subclass.
    """

    char_name: discord.ui.TextInput[discord.ui.Modal]
    realm: discord.ui.TextInput[discord.ui.Modal]
    region: discord.ui.TextInput[discord.ui.Modal]
    faction: discord.ui.TextInput[discord.ui.Modal]
    amount: discord.ui.TextInput[discord.ui.Modal]

    def __init__(self, *, title: str) -> None:
        # discord.py's Modal.__init__ validates ``title`` at construction
        # time (raises ValueError if missing). The subclass is the only
        # caller, so we accept it as a required kw-only arg and forward.
        super().__init__(title=title)
        self.char_name = discord.ui.TextInput(
            label="Character name",
            placeholder="e.g., Malesyrup",
            required=True,
            min_length=2,
            max_length=12,
        )
        self.realm = discord.ui.TextInput(
            label="Realm",
            placeholder="e.g., Stormrage",
            required=True,
            min_length=3,
            max_length=30,
        )
        self.region = discord.ui.TextInput(
            label="Region",
            placeholder="EU or NA",
            required=True,
            min_length=2,
            max_length=2,
        )
        self.faction = discord.ui.TextInput(
            label="Faction",
            placeholder="Alliance or Horde",
            required=True,
            min_length=5,
            max_length=8,
        )
        self.amount = discord.ui.TextInput(
            label="Amount (gold)",
            placeholder="e.g., 50000  (no commas, no k/m suffix)",
            required=True,
            min_length=1,
            max_length=20,
        )
        self.add_item(self.char_name)
        self.add_item(self.realm)
        self.add_item(self.region)
        self.add_item(self.faction)
        self.add_item(self.amount)


class DepositModal(_TicketInputModal):
    """Modal opened by ``/deposit``."""

    def __init__(
        self,
        *,
        on_validated: Callable[
            [discord.Interaction, DepositModalInput], Awaitable[None]
        ],
    ) -> None:
        super().__init__(title="Open deposit ticket")
        self._on_validated = on_validated

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            payload = DepositModalInput(
                char_name=self.char_name.value,
                realm=self.realm.value,
                region=self.region.value,
                faction=self.faction.value,
                amount=self.amount.value,
            )
        except ValidationError as e:
            await interaction.response.send_message(
                _format_validation_error(e),
                ephemeral=True,
            )
            return
        await self._on_validated(interaction, payload)


class WithdrawModal(_TicketInputModal):
    """Modal opened by ``/withdraw``."""

    def __init__(
        self,
        *,
        on_validated: Callable[
            [discord.Interaction, WithdrawModalInput], Awaitable[None]
        ],
    ) -> None:
        super().__init__(title="Open withdraw ticket")
        self._on_validated = on_validated

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            payload = WithdrawModalInput(
                char_name=self.char_name.value,
                realm=self.realm.value,
                region=self.region.value,
                faction=self.faction.value,
                amount=self.amount.value,
            )
        except ValidationError as e:
            await interaction.response.send_message(
                _format_validation_error(e),
                ephemeral=True,
            )
            return
        await self._on_validated(interaction, payload)


def _format_validation_error(e: ValidationError) -> str:
    """Render a ValidationError as a single-line ephemeral message.

    pydantic returns one error per offending field; we collapse to
    "field: message" lines so the user sees exactly what to fix
    without having to parse a JSON dump.
    """
    lines = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        lines.append(f"• **{loc}**: {err['msg']}")
    return "Could not open ticket — please fix:\n" + "\n".join(lines)


_DynamicEmbedSubmitCallback = Callable[
    [discord.Interaction, EditDynamicEmbedInput], Awaitable[None]
]


class EditDynamicEmbedModal(discord.ui.Modal, title="Edit guide"):
    """Modal for ``/admin-set-{deposit,withdraw}-guide`` (Story 10.3).

    Two text inputs (title + description) come up pre-filled with the
    current ``dw.dynamic_embeds`` row content so admins do small edits
    in place rather than re-typing everything. Discord limits modals to
    5 inputs total; we keep title + description in the modal and let
    admins edit colour / image / footer / fields via SQL until v1.x adds
    them as additional surfaces.

    On submit, the validator (``EditDynamicEmbedInput``) trims
    whitespace and enforces title ≤ 256 chars / description ≤ 4000
    chars (Discord's hard limits). Callers handle the persistence +
    Discord-side message edit.
    """

    title_input: discord.ui.TextInput[discord.ui.Modal]
    description_input: discord.ui.TextInput[discord.ui.Modal]

    def __init__(
        self,
        *,
        embed_key: str,
        current_title: str,
        current_description: str,
        on_validated: _DynamicEmbedSubmitCallback,
    ) -> None:
        # Use a human-readable modal title that reflects which key is
        # being edited — admins juggling deposit + withdraw guides
        # appreciate the visual distinction.
        modal_title = (
            f"Edit {embed_key.replace('_', ' ')}"
            if len(embed_key) <= 30
            else "Edit guide"
        )
        super().__init__(title=modal_title[:45])
        self.embed_key = embed_key
        self._on_validated = on_validated

        self.title_input = discord.ui.TextInput(
            label="Embed title",
            placeholder="Short heading shown above the description",
            required=True,
            min_length=1,
            max_length=256,
            default=current_title,
        )
        self.description_input = discord.ui.TextInput(
            label="Embed description",
            placeholder="Body copy — supports Markdown",
            required=True,
            min_length=1,
            max_length=4000,
            style=discord.TextStyle.paragraph,
            default=current_description,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            payload = EditDynamicEmbedInput(
                title=self.title_input.value,
                description=self.description_input.value,
            )
        except ValidationError as e:
            await interaction.response.send_message(
                _format_validation_error(e),
                ephemeral=True,
            )
            return
        await self._on_validated(interaction, payload)


__all__ = [
    "ConfirmTicketModal",
    "DepositModal",
    "EditDynamicEmbedModal",
    "WithdrawModal",
    "is_magic_word_match",
]
