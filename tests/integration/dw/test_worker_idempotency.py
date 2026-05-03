"""Worker idempotency tests (Story 14.6).

Spec §8.2 AC: every Epic 8 / 11 worker must produce the same
end-state when killed mid-execution and restarted as it would on
an uninterrupted run.

The "kill mid-execution" condition is simulated by partially
applying the side effects of a tick() (cancelling, expiring, etc.)
manually, then letting tick() pick up where it left off. The
SECURITY DEFINER fns the workers call are themselves idempotent
on already-terminal rows (each raises a sentinel the worker
swallows: ``ticket_already_terminal``, ``ticket_not_claimed``,
``cashier_not_online``), which is the underlying invariant we
exercise here.

Workers covered:

- ``ticket_timeout_worker`` (Story 8.1)
- ``claim_idle_worker``     (Story 8.2)
- ``cashier_idle_worker``   (Story 8.3)
- ``stats_aggregator``      (Story 8.5)
- ``audit_chain_verifier``  (Story 8.6)
- ``metrics_refresher``     (Story 11.1)

The online cashiers embed updater (Story 4.5) is excluded — its
tick() needs a Discord client mock and is exercised in the unit
suite under tests/unit/dw/test_live_updater.py with idempotency
already pinned (running tick twice in a row edits the same message
id without reposting).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from deathroll_core.balance.dw_manager import (
    apply_deposit_ticket,
    cancel_deposit,
    claim_ticket,
    confirm_deposit,
)

from deathroll_deposit_withdraw.metrics import REGISTRY, refresh_from_db
from deathroll_deposit_withdraw.workers.audit_chain_verifier import (
    tick as audit_chain_tick,
)
from deathroll_deposit_withdraw.workers.cashier_idle import tick as cashier_idle_tick
from deathroll_deposit_withdraw.workers.claim_idle import tick as claim_idle_tick
from deathroll_deposit_withdraw.workers.stats_aggregator import (
    tick as stats_aggregator_tick,
)
from deathroll_deposit_withdraw.workers.ticket_timeout import (
    tick as ticket_timeout_tick,
)


_USER = 44_444
_CASHIER = 9_001


# A FakeBot that just returns None from get_channel — the audit-log
# poster paths the workers call short-circuit when no channel is
# configured (we don't seed channel_id_audit_log so it stays
# ``None`` everywhere).
class _NoChannelBot:
    def get_channel(self, _id: int) -> None:
        return None


# ---------------------------------------------------------------------------
# ticket_timeout — partial completion + resume = full run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_timeout_resumes_after_partial(pool: asyncpg.Pool) -> None:
    """Set up 4 expired open deposits. Manually cancel 2 (simulating a
    crash mid-loop). Run the worker's tick — it cancels the other 2
    and returns 2. A second tick is a no-op."""
    uids: list[str] = []
    for i in range(4):
        uid = await apply_deposit_ticket(
            pool,
            discord_id=_USER + i,
            char_name=f"C{i}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=1_000,
            thread_id=1000 + i,
            parent_channel_id=2000 + i,
        )
        uids.append(uid)
    # Backdate expires_at so they all qualify as "expired".
    await pool.execute(
        "UPDATE dw.deposit_tickets SET expires_at = NOW() - INTERVAL '1 hour'"
    )

    # Simulate "killed mid-loop": cancel 2 manually.
    for uid in uids[:2]:
        await cancel_deposit(pool, ticket_uid=uid, actor_id=0, reason="partial run")

    # Worker tick — should cancel the remaining 2.
    cancelled = await ticket_timeout_tick(pool=pool, bot=_NoChannelBot())
    assert cancelled == 2

    # Second tick is a no-op — every expired ticket is now terminal.
    cancelled_again = await ticket_timeout_tick(pool=pool, bot=_NoChannelBot())
    assert cancelled_again == 0

    # All 4 land in 'cancelled'.
    statuses = await pool.fetch(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = ANY($1)", uids
    )
    assert {r["status"] for r in statuses} == {"cancelled"}


# ---------------------------------------------------------------------------
# claim_idle — partial completion + resume
# ---------------------------------------------------------------------------


async def _seed_cashier(pool: asyncpg.Pool) -> None:
    # core.users row first — release_ticket / expire_cashier write
    # audit rows with target_id = cashier_id, which FK-references
    # core.users.discord_id. In prod the cashier would've been a user
    # (e.g. ran /deposit once) before being promoted; tests have to
    # seed it explicitly.
    await pool.execute(
        "INSERT INTO core.users (discord_id) VALUES ($1) ON CONFLICT DO NOTHING",
        _CASHIER,
    )
    await pool.execute(
        "INSERT INTO dw.cashier_characters "
        "(discord_id, char_name, realm, region, faction) "
        "VALUES ($1, 'Cashier', 'Stormrage', 'EU', 'Horde')",
        _CASHIER,
    )
    await pool.execute(
        "INSERT INTO dw.cashier_status (discord_id, status, set_at, last_active_at) "
        "VALUES ($1, 'online', NOW(), NOW())",
        _CASHIER,
    )


@pytest.mark.asyncio
async def test_claim_idle_resumes_after_partial(pool: asyncpg.Pool) -> None:
    """3 claimed deposits idle >30 min. Run tick once. Run again.
    Second tick is a no-op (released tickets are now back to 'open',
    not 'claimed', so nothing matches the predicate)."""
    await _seed_cashier(pool)
    uids: list[str] = []
    for i in range(3):
        uid = await apply_deposit_ticket(
            pool,
            discord_id=_USER + i,
            char_name=f"C{i}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=1_000,
            thread_id=3000 + i,
            parent_channel_id=4000 + i,
        )
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
        )
        uids.append(uid)
    # Backdate last_activity_at so claim_idle's 30-min predicate matches.
    await pool.execute(
        "UPDATE dw.deposit_tickets "
        "SET last_activity_at = NOW() - INTERVAL '1 hour' "
        "WHERE ticket_uid = ANY($1)",
        uids,
    )

    summary1 = await claim_idle_tick(pool=pool, bot=_NoChannelBot())
    assert summary1.released == 3

    # Second tick — every ticket is now 'open', the SELECT returns 0.
    summary2 = await claim_idle_tick(pool=pool, bot=_NoChannelBot())
    assert summary2.released == 0
    assert summary2.cancelled == 0


# ---------------------------------------------------------------------------
# cashier_idle — partial completion + resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cashier_idle_resumes_after_partial(pool: asyncpg.Pool) -> None:
    """3 cashiers online, all idle >1h. tick() flips them all offline.
    A second tick is a no-op."""
    for cashier_id in (9001, 9002, 9003):
        # Each cashier needs a core.users row for the audit_log FK on
        # the auto-offline event (target_id = cashier_id).
        await pool.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) ON CONFLICT DO NOTHING",
            cashier_id,
        )
        await pool.execute(
            "INSERT INTO dw.cashier_characters "
            "(discord_id, char_name, realm, region, faction) "
            "VALUES ($1, $2, 'Stormrage', 'EU', 'Horde')",
            cashier_id,
            f"Cashier{cashier_id}",
        )
        await pool.execute(
            "INSERT INTO dw.cashier_status "
            "(discord_id, status, set_at, last_active_at) "
            "VALUES ($1, 'online', NOW(), NOW() - INTERVAL '2 hours') "
            "ON CONFLICT (discord_id) DO UPDATE SET "
            "  status='online', last_active_at = NOW() - INTERVAL '2 hours'",
            cashier_id,
        )

    expired1 = await cashier_idle_tick(pool=pool)
    assert expired1 == 3

    expired2 = await cashier_idle_tick(pool=pool)
    assert expired2 == 0

    # All 3 cashier_status rows now read 'offline'.
    statuses = await pool.fetch(
        "SELECT status FROM dw.cashier_status WHERE discord_id IN (9001, 9002, 9003)"
    )
    assert {r["status"] for r in statuses} == {"offline"}


# ---------------------------------------------------------------------------
# stats_aggregator — running twice produces identical numbers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_aggregator_is_idempotent(pool: asyncpg.Pool) -> None:
    """Run a couple of confirm flows so cashier_stats has rows. Run
    the aggregator tick(); snapshot. Run again; snapshot must match."""
    await _seed_cashier(pool)
    for i in range(3):
        uid = await apply_deposit_ticket(
            pool,
            discord_id=_USER + i,
            char_name=f"C{i}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=1_000,
            thread_id=5000 + i,
            parent_channel_id=6000 + i,
        )
        await claim_ticket(
            pool, ticket_type="deposit", ticket_uid=uid, cashier_id=_CASHIER
        )
        await confirm_deposit(pool, ticket_uid=uid, cashier_id=_CASHIER)

    updated1 = await stats_aggregator_tick(pool=pool)

    # Snapshot the cashier_stats row.
    snap1 = await pool.fetchrow(
        "SELECT avg_claim_to_confirm_s, total_online_seconds, "
        "       deposits_completed, total_volume_g "
        "FROM dw.cashier_stats WHERE discord_id = $1",
        _CASHIER,
    )

    updated2 = await stats_aggregator_tick(pool=pool)
    snap2 = await pool.fetchrow(
        "SELECT avg_claim_to_confirm_s, total_online_seconds, "
        "       deposits_completed, total_volume_g "
        "FROM dw.cashier_stats WHERE discord_id = $1",
        _CASHIER,
    )

    assert updated1 == updated2 == 1
    assert dict(snap1) == dict(snap2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# audit_chain_verifier — running twice walks the same range, then 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_chain_verifier_is_idempotent(pool: asyncpg.Pool) -> None:
    """First tick walks all rows; second tick has nothing new to walk
    (last_verified_audit_row_id advanced past the tail)."""
    # Generate audit rows.
    for i in range(3):
        await apply_deposit_ticket(
            pool,
            discord_id=_USER + i,
            char_name=f"C{i}",
            realm="Stormrage",
            region="EU",
            faction="Horde",
            amount=1_000,
            thread_id=7000 + i,
            parent_channel_id=8000 + i,
        )

    result1 = await audit_chain_tick(pool=pool)
    assert result1.broken_at_id is None
    assert result1.checked_count >= 3

    result2 = await audit_chain_tick(pool=pool)
    assert result2.broken_at_id is None
    # The second tick re-checks the boundary (last_verified_id) so
    # checked_count is 0 or 1; importantly broken_at stays None.
    assert result2.checked_count <= 1
    assert result2.last_verified_id == result1.last_verified_id


# ---------------------------------------------------------------------------
# metrics_refresher — pure DB-read; running twice yields the same scrape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_refresher_is_idempotent(pool: asyncpg.Pool) -> None:
    """Refresh once, snapshot the registry; refresh again, snapshot;
    the two scrapes must match line-for-line (same DB → same gauges)."""
    import prometheus_client

    await refresh_from_db(pool=pool)
    scrape1 = prometheus_client.generate_latest(REGISTRY).decode("utf-8")

    await refresh_from_db(pool=pool)
    scrape2 = prometheus_client.generate_latest(REGISTRY).decode("utf-8")

    # Strip auto-generated stuff that legitimately differs (none for our
    # custom registry — no process_/python_/gc_ defaults).
    assert scrape1 == scrape2


# ---------------------------------------------------------------------------
# Bonus — ticket_timeout doesn't cancel a NOT-YET-expired ticket
# (catches a regression where someone might tighten the predicate too
# aggressively).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_timeout_skips_unexpired_tickets(pool: asyncpg.Pool) -> None:
    """A ticket whose expires_at is still in the future must NOT be
    touched by the worker — only NOW() > expires_at qualifies."""
    uid = await apply_deposit_ticket(
        pool,
        discord_id=_USER,
        char_name="C",
        realm="Stormrage",
        region="EU",
        faction="Horde",
        amount=1_000,
        thread_id=9_999,
        parent_channel_id=8_888,
    )
    # Push expires_at well into the future.
    future = datetime.now(UTC) + timedelta(hours=24)
    await pool.execute(
        "UPDATE dw.deposit_tickets SET expires_at = $1 WHERE ticket_uid = $2",
        future,
        uid,
    )

    cancelled = await ticket_timeout_tick(pool=pool, bot=_NoChannelBot())
    assert cancelled == 0

    status = await pool.fetchval(
        "SELECT status FROM dw.deposit_tickets WHERE ticket_uid = $1", uid
    )
    assert status == "open"
