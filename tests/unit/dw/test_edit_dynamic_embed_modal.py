"""Tests for ``EditDynamicEmbedModal`` (Story 10.3).

Story 10.3 ships ``/admin-set-deposit-guide`` and
``/admin-set-withdraw-guide`` modals that pre-fill the current
title + description from ``dw.dynamic_embeds`` and write the new
content back on submit. The modal itself is a discord.py
``ui.Modal`` subclass with two text inputs; this test file
exercises only its construction (the submit path runs Discord-
specific code we cover in integration tests).

discord.py's ``ui.View.__init__`` calls ``asyncio.get_running_loop()``
to create an internal Future, so each test wraps construction in
``asyncio.run(...)`` to provide a loop.
"""

from __future__ import annotations

import asyncio

from deathroll_deposit_withdraw.views.modals import EditDynamicEmbedModal


async def _noop(_interaction, _payload):  # type: ignore[no-untyped-def]
    return None


def _make_modal(
    *,
    embed_key: str = "how_to_deposit",
    current_title: str = "How to deposit",
    current_description: str = "Run /deposit, follow cashier instructions.",
) -> EditDynamicEmbedModal:
    async def _build() -> EditDynamicEmbedModal:
        return EditDynamicEmbedModal(
            embed_key=embed_key,
            current_title=current_title,
            current_description=current_description,
            on_validated=_noop,
        )

    return asyncio.run(_build())


def test_edit_dynamic_embed_modal_prefills_inputs() -> None:
    modal = _make_modal()
    assert modal.title_input.default == "How to deposit"
    assert modal.description_input.default == "Run /deposit, follow cashier instructions."


def test_edit_dynamic_embed_modal_carries_embed_key() -> None:
    """The embed_key flows through the modal so the on_submit handler
    knows which dynamic_embeds row to UPDATE."""
    modal = _make_modal(
        embed_key="how_to_withdraw",
        current_title="t",
        current_description="d",
    )
    assert modal.embed_key == "how_to_withdraw"


def test_edit_dynamic_embed_modal_title_label_visible_to_user() -> None:
    """Discord shows the field label above the input — must be human
    readable, not a snake_case key."""
    modal = _make_modal(current_title="t", current_description="d")
    assert "title" in modal.title_input.label.lower()
    assert "description" in modal.description_input.label.lower()
