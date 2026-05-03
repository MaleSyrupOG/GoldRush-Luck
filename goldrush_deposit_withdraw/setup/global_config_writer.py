"""Persist channel ids produced by the channel factory into ``dw.global_config``.

This is the integration glue between Story 3.4 (the pure-function
channel factory) and Story 10.1 (the ``/admin setup`` command).
The channel factory returns a ``SetupReport`` carrying the new
channel ids; this writer turns each into a row in
``dw.global_config`` keyed ``channel_id_<key>`` so the rest of
the bot can read them at runtime via
``goldrush_core.discord_helpers.channel_binding.resolve_channel_id``.

The UPSERT pattern means re-running ``/admin setup`` after, say,
the operator deleted and recreated a channel will overwrite the
stale id without creating a duplicate row.
"""

from __future__ import annotations

import structlog
from goldrush_core.db import Executor

_log = structlog.get_logger(__name__)


_UPSERT = """
INSERT INTO dw.global_config (key, value_int, updated_by, updated_at)
VALUES ($1, $2, $3, NOW())
ON CONFLICT (key) DO UPDATE SET
    value_int  = EXCLUDED.value_int,
    updated_by = EXCLUDED.updated_by,
    updated_at = NOW()
"""


async def persist_channel_ids(
    executor: Executor,
    *,
    channel_id_map: dict[str, int],
    actor_id: int,
) -> None:
    """Write each ``{key: discord_id}`` entry as ``channel_id_<key>``.

    Skips silently when the map is empty (e.g., dry-run produced
    no real ids). The ``actor_id`` is the admin who ran
    ``/admin setup``; it lands on the row's ``updated_by`` column
    so the audit trail attributes the change.
    """
    for key, channel_id in channel_id_map.items():
        config_key = f"channel_id_{key}"
        await executor.execute(_UPSERT, config_key, channel_id, actor_id)
        _log.info(
            "global_config_channel_id_written",
            key=config_key,
            channel_id=channel_id,
            actor_id=actor_id,
        )


async def persist_role_ids(
    executor: Executor,
    *,
    role_id_map: dict[str, int],
    actor_id: int,
) -> None:
    """Write each ``{key: role_id}`` entry as ``role_id_<key>``.

    Used by ``/admin-setup`` so subsequent slash commands can render
    real role mentions (``<@&id>``) instead of literal strings
    (``@cashier``) — Discord treats the literal as plain text and
    never fires the role ping. Empty map is a no-op (consistent
    with ``persist_channel_ids``).
    """
    for key, role_id in role_id_map.items():
        config_key = f"role_id_{key}"
        await executor.execute(_UPSERT, config_key, role_id, actor_id)
        _log.info(
            "global_config_role_id_written",
            key=config_key,
            role_id=role_id,
            actor_id=actor_id,
        )


async def persist_config_int(
    executor: Executor,
    *,
    key: str,
    value: int,
    actor_id: int,
) -> None:
    """UPSERT a single integer-typed ``dw.global_config`` row.

    Used by Story 10.2 (``/admin-set-deposit-limits``,
    ``/admin-set-withdraw-limits``, ``/admin-set-fee-withdraw``) and
    by Story 8.6's audit chain verifier (``last_verified_audit_row_id``,
    although that one writes inline rather than going through this
    helper because its key is internal-only).

    Unlike ``persist_channel_ids`` and ``persist_role_ids``, this writer
    does NOT prefix the key — caller passes the canonical key as-is
    (e.g. ``"min_deposit_g"``, ``"withdraw_fee_bps"``). The bot's
    config-reader helpers and the seed migration ``0005`` already use
    those bare keys, so any prefix here would create a parallel set of
    rows.
    """
    await executor.execute(_UPSERT, key, value, actor_id)
    _log.info(
        "global_config_int_written",
        key=key,
        value=value,
        actor_id=actor_id,
    )


__all__ = [
    "persist_channel_ids",
    "persist_config_int",
    "persist_role_ids",
]
