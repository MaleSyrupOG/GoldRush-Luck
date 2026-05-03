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


def _parse_amount(amount_str: str) -> int | None:
    """Parse a re-typed amount, forgiving thousand separators.

    Returns the int on success, ``None`` if the input isn't an
    integer at all. The forgiveness (commas + underscores) reduces
    UX friction without weakening the 2FA: the typed VALUE has to
    still match the original ``amount`` exactly, just spelled with
    a thousands separator if the operator likes.
    """
    cleaned = amount_str.strip().replace(",", "").replace("_", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def validate_treasury_sweep_confirm(
    *,
    magic_word: str,
    amount_str: str,
    expected_amount: int,
) -> str | None:
    """Validate the treasury-sweep 2FA modal payload.

    Returns ``None`` when the operator typed the magic word ``SWEEP``
    AND the same amount they put in the slash command. On any failure,
    returns a single-line, ephemeral-friendly error string explaining
    which check failed.
    """
    if not is_magic_word_match(supplied=magic_word, expected="SWEEP"):
        return "❌ Cancelled — expected to read `SWEEP` exactly."
    re_typed = _parse_amount(amount_str)
    if re_typed is None:
        return f"❌ Cancelled — amount `{amount_str}` is not an integer."
    if re_typed != expected_amount:
        return (
            f"❌ Cancelled — amount mismatch (typed {re_typed:,}, "
            f"expected {expected_amount:,})."
        )
    return None


def validate_treasury_withdraw_confirm(
    *,
    magic_word: str,
    amount_str: str,
    expected_amount: int,
    user_id_str: str,
    expected_user_id: int,
) -> str | None:
    """Validate the treasury-withdraw 2FA modal payload.

    Same shape as :func:`validate_treasury_sweep_confirm` plus a
    re-typed user id check — the operator MUST re-type the snowflake
    of the recipient so a typo in the slash command can't move gold
    to the wrong user. The magic word is ``TREASURY-WITHDRAW``.
    """
    if not is_magic_word_match(supplied=magic_word, expected="TREASURY-WITHDRAW"):
        return "❌ Cancelled — expected to read `TREASURY-WITHDRAW` exactly."
    re_typed_amount = _parse_amount(amount_str)
    if re_typed_amount is None:
        return f"❌ Cancelled — amount `{amount_str}` is not an integer."
    if re_typed_amount != expected_amount:
        return (
            f"❌ Cancelled — amount mismatch (typed {re_typed_amount:,}, "
            f"expected {expected_amount:,})."
        )
    try:
        re_typed_user = int(user_id_str.strip())
    except ValueError:
        return f"❌ Cancelled — user id `{user_id_str}` is not numeric."
    if re_typed_user != expected_user_id:
        return (
            "❌ Cancelled — user id mismatch (typed "
            f"`{re_typed_user}`, expected `{expected_user_id}`)."
        )
    return None


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


_TreasurySweepConfirmCallback = Callable[[discord.Interaction], Awaitable[None]]


class TreasurySweepConfirmModal(discord.ui.Modal, title="Treasury sweep — confirm"):
    """Two-input 2FA modal for ``/admin-treasury-sweep`` (Story 10.6).

    The operator re-types the magic word ``SWEEP`` AND the amount they
    just typed in the slash command. Mismatch on either input cancels
    the sweep with a clear ephemeral; only when both pass does the
    callback fire (which calls ``dw.treasury_sweep``).
    """

    magic_word_input: discord.ui.TextInput[discord.ui.Modal]
    amount_input: discord.ui.TextInput[discord.ui.Modal]

    def __init__(
        self,
        *,
        expected_amount: int,
        on_confirm: _TreasurySweepConfirmCallback,
    ) -> None:
        super().__init__()
        self._expected_amount = expected_amount
        self._on_confirm = on_confirm
        self.magic_word_input = discord.ui.TextInput(
            label="Type SWEEP to confirm",
            placeholder="SWEEP",
            required=True,
            min_length=5,
            max_length=8,
        )
        self.amount_input = discord.ui.TextInput(
            label=f"Re-type amount ({expected_amount:,})",
            placeholder=f"{expected_amount}",
            required=True,
            min_length=1,
            max_length=20,
        )
        self.add_item(self.magic_word_input)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        err = validate_treasury_sweep_confirm(
            magic_word=self.magic_word_input.value,
            amount_str=self.amount_input.value,
            expected_amount=self._expected_amount,
        )
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await self._on_confirm(interaction)


class TreasuryWithdrawConfirmModal(
    discord.ui.Modal, title="Treasury withdraw-to-user — confirm"
):
    """Three-input 2FA modal for ``/admin-treasury-withdraw-to-user``.

    The operator re-types the magic word ``TREASURY-WITHDRAW``, the
    amount, AND the target user id. Re-typing the user id is the
    extra guard against a slash-command-time autocomplete picking the
    wrong user.
    """

    magic_word_input: discord.ui.TextInput[discord.ui.Modal]
    amount_input: discord.ui.TextInput[discord.ui.Modal]
    user_id_input: discord.ui.TextInput[discord.ui.Modal]

    def __init__(
        self,
        *,
        expected_amount: int,
        expected_user_id: int,
        on_confirm: _TreasurySweepConfirmCallback,
    ) -> None:
        super().__init__()
        self._expected_amount = expected_amount
        self._expected_user_id = expected_user_id
        self._on_confirm = on_confirm
        self.magic_word_input = discord.ui.TextInput(
            label="Type TREASURY-WITHDRAW",
            placeholder="TREASURY-WITHDRAW",
            required=True,
            min_length=17,
            max_length=20,
        )
        self.amount_input = discord.ui.TextInput(
            label=f"Re-type amount ({expected_amount:,})",
            placeholder=f"{expected_amount}",
            required=True,
            min_length=1,
            max_length=20,
        )
        self.user_id_input = discord.ui.TextInput(
            label=f"Re-type recipient user id ({expected_user_id})",
            placeholder=f"{expected_user_id}",
            required=True,
            min_length=10,
            max_length=24,
        )
        self.add_item(self.magic_word_input)
        self.add_item(self.amount_input)
        self.add_item(self.user_id_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        err = validate_treasury_withdraw_confirm(
            magic_word=self.magic_word_input.value,
            amount_str=self.amount_input.value,
            expected_amount=self._expected_amount,
            user_id_str=self.user_id_input.value,
            expected_user_id=self._expected_user_id,
        )
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await self._on_confirm(interaction)


__all__ = [
    "ConfirmTicketModal",
    "DepositModal",
    "EditDynamicEmbedModal",
    "TreasurySweepConfirmModal",
    "TreasuryWithdrawConfirmModal",
    "WithdrawModal",
    "is_magic_word_match",
    "validate_treasury_sweep_confirm",
    "validate_treasury_withdraw_confirm",
]
