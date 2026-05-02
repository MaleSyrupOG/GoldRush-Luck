"""Channel binding: look up the configured Discord channel id for a key.

Channel ids are persisted in ``dw.global_config`` under keys of the
form ``channel_id_<key>`` (written by ``/admin setup`` once Story
10.x lands). At runtime, slash commands fetch the configured id
to validate "this command must be used in <#channel>" or to post
into the right channel (e.g., cashier alerts).

The canonical keys are the ones the channel factory creates in
Story 3.4 (account, admin, cashier, deposit, ticket, withdraw —
plus the welcome / online-cashiers static embeds). A typo in a
key surfaces as ``ValueError`` rather than a silent miss.
"""

from __future__ import annotations

from goldrush_core.db import Executor

# Canonical keys — the same set the channel factory provisions
# (Story 3.4) plus the dynamic-embed keys consumed by Stories
# 4.4 / 4.5. Anything not in here is a typo.
CANONICAL_KEYS: frozenset[str] = frozenset(
    {
        "how_to_deposit",
        "how_to_withdraw",
        "deposit",
        "withdraw",
        "online_cashiers",
        "cashier_alerts",
        "cashier_onboarding",
        "disputes",
    }
)


async def resolve_channel_id(executor: Executor, key: str) -> int | None:
    """Return the channel id stored at ``channel_id_<key>`` in dw.global_config.

    ``None`` means the operator hasn't run ``/admin setup`` yet (or
    the channel was unbound). Callers map ``None`` to a friendly
    ephemeral error rather than crashing.
    """
    if key not in CANONICAL_KEYS:
        raise ValueError(
            f"unknown canonical channel key {key!r}; "
            f"valid keys are {sorted(CANONICAL_KEYS)}"
        )
    row = await executor.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = $1",
        f"channel_id_{key}",
    )
    if row is None or row["value_int"] is None:
        return None
    return int(row["value_int"])


__all__ = ["CANONICAL_KEYS", "resolve_channel_id"]
