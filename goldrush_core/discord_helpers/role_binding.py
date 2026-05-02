"""Role binding: look up persisted Discord role ids from ``dw.global_config``.

Role ids are written by ``/admin-setup`` (Story 10.1) under keys
``role_id_<key>`` so the bot can render real ``<@&id>`` role
mentions instead of literal ``@cashier`` strings (which Discord
treats as plain text and never pings).

The canonical keys today are:

- ``cashier`` — the role compatible cashiers carry; pinged in
  every ticket thread and in ``#cashier-alerts``.
- ``admin``   — staff role used in escalations / treasury alerts;
  reserved for future commands.

The lookup is best-effort: if the operator hasn't run
``/admin-setup`` yet (or ran it without the ``cashier_role`` /
``admin_role`` parameters), the helper returns ``None`` and the
caller falls back to a literal ``@<key>`` so the message still
makes sense to a human even though it doesn't actually ping.
"""

from __future__ import annotations

from goldrush_core.db import Executor

CANONICAL_ROLE_KEYS: frozenset[str] = frozenset({"cashier", "admin"})


async def resolve_role_id(executor: Executor, key: str) -> int | None:
    """Read the role snowflake stored at ``role_id_<key>`` in dw.global_config.

    ``None`` means the operator hasn't bound the role yet via
    ``/admin-setup``. Callers map ``None`` to a literal-string
    fallback rather than raising.
    """
    if key not in CANONICAL_ROLE_KEYS:
        raise ValueError(
            f"unknown canonical role key {key!r}; "
            f"valid keys are {sorted(CANONICAL_ROLE_KEYS)}"
        )
    row = await executor.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = $1",
        f"role_id_{key}",
    )
    if row is None or row["value_int"] is None:
        return None
    return int(row["value_int"])


async def role_mention(executor: Executor, key: str) -> str:
    """Return ``<@&role_id>`` (a real ping) or ``@<key>`` (plain text fallback).

    The fallback is intentional: ``/admin-setup`` is meant to be
    run before tickets start opening, but if a cashier triggers a
    flow before the role id is bound the ping degrades to the
    literal ``@cashier`` so the message still reads sensibly. The
    cashier still sees the alert via the ``#cashier-alerts``
    channel embed even without the role ping.
    """
    role_id = await resolve_role_id(executor, key)
    if role_id is not None:
        return f"<@&{role_id}>"
    return f"@{key}"


__all__ = [
    "CANONICAL_ROLE_KEYS",
    "resolve_role_id",
    "role_mention",
]
