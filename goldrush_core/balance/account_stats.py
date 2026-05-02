"""Account-stats query for the user-facing ``/balance`` command.

The four metrics the user sees on ``/balance`` come from a single
SQL query that joins ``core.balances`` with aggregated subqueries
over ``dw.deposit_tickets`` and ``dw.withdraw_tickets``. Anchoring
the join on ``core.users`` (rather than ``core.balances``) is what
lets us return ``None`` when the user has no row yet — the cog
then renders the redirect-to-deposit embed instead of zeros.

The query reads from ``core.users`` / ``core.balances`` /
``dw.deposit_tickets`` / ``dw.withdraw_tickets``. The bot's
``goldrush_dw`` role has SELECT on all of those (per migration
0001 and 0003 grants).
"""

from __future__ import annotations

from dataclasses import dataclass

from goldrush_core.db import Executor

_QUERY = """
SELECT
    COALESCE(b.balance, 0)                      AS balance,
    COALESCE(d.total_deposited, 0)              AS total_deposited,
    COALESCE(w.total_withdrawn, 0)              AS total_withdrawn,
    COALESCE(w.total_fee, 0)                    AS lifetime_fee_paid
FROM core.users u
LEFT JOIN core.balances b ON b.discord_id = u.discord_id
LEFT JOIN (
    SELECT discord_id, SUM(amount) AS total_deposited
    FROM dw.deposit_tickets
    WHERE discord_id = $1 AND status = 'confirmed'
    GROUP BY discord_id
) d ON d.discord_id = u.discord_id
LEFT JOIN (
    SELECT discord_id,
           SUM(amount) AS total_withdrawn,
           SUM(fee)    AS total_fee
    FROM dw.withdraw_tickets
    WHERE discord_id = $1 AND status = 'confirmed'
    GROUP BY discord_id
) w ON w.discord_id = u.discord_id
WHERE u.discord_id = $1
"""


@dataclass(frozen=True)
class AccountStats:
    """Frozen snapshot returned by ``fetch_account_stats``.

    All four fields are ``BIGINT`` from Postgres; we expose them as
    plain ``int``. The frozen-dataclass guarantee mirrors the rest of
    the system (``BalanceSnapshot``, ``DepositTicket``, …) so a stats
    instance carrying through several Discord-render layers cannot be
    silently mutated.
    """

    balance: int
    total_deposited: int
    total_withdrawn: int
    lifetime_fee_paid: int


async def fetch_account_stats(
    executor: Executor, *, discord_id: int
) -> AccountStats | None:
    """Return the account stats for ``discord_id`` or ``None`` if unregistered.

    A ``None`` return tells the cog to render
    ``no_balance_embed`` (with a deep-link to ``#how-to-deposit``);
    every other path renders ``account_summary_embed``.
    """
    row = await executor.fetchrow(_QUERY, discord_id)
    if row is None:
        return None
    return AccountStats(
        balance=int(row["balance"]),
        total_deposited=int(row["total_deposited"]),
        total_withdrawn=int(row["total_withdrawn"]),
        lifetime_fee_paid=int(row["lifetime_fee_paid"]),
    )


__all__ = ["AccountStats", "fetch_account_stats"]
