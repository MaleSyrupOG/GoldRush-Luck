"""Audit chain verifier worker (Story 8.6).

Walks ``core.audit_log`` from the persisted
``last_verified_audit_row_id`` (in ``dw.global_config``)
recomputing the HMAC chain via ``core.verify_audit_chain``
(SECURITY DEFINER fn from migration ``0017_core_audit_chain_verifier``).

Behaviour:

- **Healthy chain** — advances the persisted id and logs an INFO
  ``audit_chain_verified`` event with the count.
- **Empty range** — checked_count = 0; the worker skips the UPSERT
  to avoid pointless writes (tickle every 6 h on an idle chain
  shouldn't churn the global_config row).
- **Chain break** — emits a CRITICAL-level ``audit_chain_break``
  structlog event with the ``broken_at_id``. Loki collects, the
  alerting layer (Story 11.3 — Alertmanager rules) escalates.
  The persisted id is INTENTIONALLY NOT advanced so the next
  iteration re-checks the same range; admins fix the underlying
  data and a re-run validates.

The worker also ships an "on demand" entry point: the ``/admin
verify-audit`` slash command (Story 10.8 / Luck §11.4 — landed in
this same Story 8.6 commit because both surfaces hit the same
SECURITY DEFINER fn).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from goldrush_core.db import Executor

from goldrush_deposit_withdraw.workers._periodic import PeriodicWorker

_log = structlog.get_logger(__name__)


# Cap rows per iteration so a giant backlog after a downtime window
# doesn't hold the worker for minutes. 1000 rows per 6-hour tick
# converges to several hundred thousand events / day, well over our
# expected volume; if we ever hit it, the worker simply iterates
# faster on subsequent ticks.
_MAX_ROWS_PER_TICK = 1000


_GLOBAL_CONFIG_KEY = "last_verified_audit_row_id"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of one iteration. ``broken_at_id is None`` is healthy."""

    checked_count: int
    last_verified_id: int
    broken_at_id: int | None


async def tick(*, pool: Executor) -> VerifyResult:
    """Run one verification pass — returns the outcome.

    Reads the resume pointer from ``dw.global_config``, calls the
    verifier SDF, advances the pointer on success, logs CRITICAL on
    break.
    """
    last_id = await _read_last_verified(pool)

    row = await pool.fetchrow(
        "SELECT * FROM core.verify_audit_chain($1, $2)",
        last_id,
        _MAX_ROWS_PER_TICK,
    )
    if row is None:
        # Should not happen — RETURNS TABLE always returns one row.
        # Defensive log + treat as no-op.
        _log.warning("audit_chain_verifier_empty_response")
        return VerifyResult(checked_count=0, last_verified_id=last_id, broken_at_id=None)

    checked = int(row["checked_count"])
    new_last = int(row["last_verified_id"])
    broken_obj = row["broken_at_id"]
    broken: int | None = int(broken_obj) if broken_obj is not None else None

    if broken is not None:
        # Loud, structured, machine-greppable. Story 11.3 will route this
        # to Alertmanager; for now the operator finds it via Loki.
        _log.critical(
            "audit_chain_break",
            broken_at_id=broken,
            last_verified_id=new_last,
            checked_count=checked,
        )
        return VerifyResult(
            checked_count=checked,
            last_verified_id=new_last,
            broken_at_id=broken,
        )

    if checked > 0:
        await _persist_last_verified(pool, new_last)
        _log.info(
            "audit_chain_verified",
            checked_count=checked,
            last_verified_id=new_last,
        )
    return VerifyResult(
        checked_count=checked,
        last_verified_id=new_last,
        broken_at_id=None,
    )


async def _read_last_verified(pool: Executor) -> int:
    """Return the persisted resume pointer, defaulting to 0 if absent."""
    row = await pool.fetchrow(
        "SELECT value_int FROM dw.global_config WHERE key = $1",
        _GLOBAL_CONFIG_KEY,
    )
    if row is None or row["value_int"] is None:
        return 0
    return int(row["value_int"])


async def _persist_last_verified(pool: Executor, new_id: int) -> None:
    """UPSERT the resume pointer; written by the system actor (id=0)."""
    await pool.execute(
        """
        INSERT INTO dw.global_config (key, value_int, updated_by, updated_at)
        VALUES ($1, $2, 0, NOW())
        ON CONFLICT (key) DO UPDATE
            SET value_int = EXCLUDED.value_int,
                updated_by = 0,
                updated_at = NOW()
        """,
        _GLOBAL_CONFIG_KEY,
        new_id,
    )


class AuditChainVerifierWorker(PeriodicWorker):
    """Cancellable loop wrapping :func:`tick` every 6 h by default."""

    def __init__(
        self,
        *,
        pool: Executor,
        interval_seconds: float = 6 * 60 * 60,  # 6 hours
    ) -> None:
        super().__init__(
            name="audit_chain_verifier", interval_seconds=interval_seconds
        )
        self._pool = pool

    async def tick(self) -> None:
        await tick(pool=self._pool)


__all__ = ["AuditChainVerifierWorker", "VerifyResult", "tick"]
