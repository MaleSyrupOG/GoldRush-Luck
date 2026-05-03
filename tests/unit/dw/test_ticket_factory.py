"""Unit tests for `deathroll_deposit_withdraw.tickets.factory`.

The factory creates the per-ticket private thread under the
``#deposit`` / ``#withdraw`` parent channel and adds the ticket
owner. The discord.py thread API is mocked.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from deathroll_deposit_withdraw.tickets.factory import create_ticket_thread


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeThread:
    def __init__(self, *, name: str, type_: Any, invitable: bool, archive: int) -> None:
        self.id = 9999
        self.name = name
        self.type = type_
        self.invitable = invitable
        self.auto_archive_duration = archive
        self.added_users: list[_FakeUser] = []

    async def add_user(self, user: _FakeUser) -> None:
        self.added_users.append(user)


class _FakeParent:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    async def create_thread(
        self,
        *,
        name: str,
        type: Any,
        invitable: bool,
        auto_archive_duration: int,
        reason: str | None = None,
    ) -> _FakeThread:
        self.create_calls.append(
            {
                "name": name,
                "type": type,
                "invitable": invitable,
                "auto_archive_duration": auto_archive_duration,
                "reason": reason,
            }
        )
        return _FakeThread(
            name=name, type_=type, invitable=invitable, archive=auto_archive_duration
        )


def test_creates_private_thread_with_ticket_uid_name() -> None:
    parent = _FakeParent()
    user = _FakeUser(user_id=42)

    async def _exercise() -> _FakeThread:
        return await create_ticket_thread(
            parent=parent,  # type: ignore[arg-type]
            name="deposit-1",
            user=user,  # type: ignore[arg-type]
        )

    thread = asyncio.run(_exercise())

    # Exactly one create call with the spec parameters.
    assert len(parent.create_calls) == 1
    call = parent.create_calls[0]
    assert call["name"] == "deposit-1"
    assert call["invitable"] is False
    assert call["auto_archive_duration"] == 1440
    # The user has been added explicitly (private threads require this).
    assert len(thread.added_users) == 1
    assert thread.added_users[0].id == 42


def test_create_thread_propagates_reason_for_audit() -> None:
    """``reason`` is the audit-log reason Discord shows in the
    server's audit log; we always set it so admins can trace
    every thread back to a ticket."""
    parent = _FakeParent()
    user = _FakeUser(user_id=42)

    asyncio.run(
        create_ticket_thread(
            parent=parent,  # type: ignore[arg-type]
            name="deposit-1",
            user=user,  # type: ignore[arg-type]
            reason="DeathRoll deposit ticket",
        )
    )
    assert "deposit" in (parent.create_calls[0]["reason"] or "")


def test_invalid_name_rejected() -> None:
    """Discord enforces 1-100 char names; we add a defensive guard
    so a programming bug doesn't surface as an opaque HTTP 400."""
    parent = _FakeParent()
    user = _FakeUser(user_id=42)
    with pytest.raises(ValueError):
        asyncio.run(
            create_ticket_thread(
                parent=parent,  # type: ignore[arg-type]
                name="",  # empty
                user=user,  # type: ignore[arg-type]
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(
            create_ticket_thread(
                parent=parent,  # type: ignore[arg-type]
                name="x" * 101,
                user=user,  # type: ignore[arg-type]
            )
        )
