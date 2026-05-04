"""Story 3.4 — Fairness API orchestrator.

Spec ref: Luck design §4.6, §4.4.

The single async entry point every game cog calls to draw
randomness for a bet:

    ticket = await request_outcome_bytes(
        pool, discord_id=user_id, byte_count=64, game_context="coinflip"
    )

The function:
1. Locks the user's user_seeds row FOR UPDATE inside a transaction
   (so a concurrent rotate_user_seed cannot slip between the
   nonce increment and the seed read).
2. Calls fairness.next_nonce SDF to atomically allocate the next
   nonce.
3. Reads the raw server_seed (sensitive, never logged or returned).
4. Computes HMAC-SHA512(server_seed, f"{client_seed}:{nonce}").
5. If byte_count > 64, extends with the same SHA-256 chain as the
   decoders.
6. Returns a FairnessTicket carrying ONLY the public state +
   the hmac_bytes — the server_seed never escapes the function.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac

import asyncpg
import pytest
from deathroll_core.fairness.api import FairnessTicket, request_outcome_bytes
from deathroll_core.fairness.seeds import ensure_seeds

pytestmark = pytest.mark.asyncio


async def _bootstrap_user(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool, *, user_id: int
) -> None:
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            user_id,
        )
    await ensure_seeds(luck_pool, discord_id=user_id)


# ---------------------------------------------------------------------------
# Shape + redaction
# ---------------------------------------------------------------------------


async def test_ticket_carries_only_public_fields(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _bootstrap_user(luck_pool, admin_pool, user_id=60001)
    ticket = await request_outcome_bytes(
        luck_pool, discord_id=60001, byte_count=64, game_context="test"
    )
    assert isinstance(ticket, FairnessTicket)
    fields = set(FairnessTicket.model_fields)
    assert "server_seed" not in fields
    assert fields == {"hmac_bytes", "server_seed_hash", "client_seed", "nonce"}


async def test_ticket_first_call_starts_at_nonce_zero(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _bootstrap_user(luck_pool, admin_pool, user_id=60002)
    ticket = await request_outcome_bytes(
        luck_pool, discord_id=60002, byte_count=64, game_context="t"
    )
    assert ticket.nonce == 0


async def test_ticket_subsequent_call_advances_nonce(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    await _bootstrap_user(luck_pool, admin_pool, user_id=60003)
    t1 = await request_outcome_bytes(
        luck_pool, discord_id=60003, byte_count=64, game_context="t"
    )
    t2 = await request_outcome_bytes(
        luck_pool, discord_id=60003, byte_count=64, game_context="t"
    )
    t3 = await request_outcome_bytes(
        luck_pool, discord_id=60003, byte_count=64, game_context="t"
    )
    assert (t1.nonce, t2.nonce, t3.nonce) == (0, 1, 2)


# ---------------------------------------------------------------------------
# Output integrity
# ---------------------------------------------------------------------------


async def test_ticket_hmac_bytes_matches_engine(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """The hmac_bytes returned in the ticket equals what the engine
    would produce from the (server_seed, client_seed, nonce) the
    user has committed to. We verify by reading the raw seed via
    admin (NOT through the public API) and computing independently.
    """
    await _bootstrap_user(luck_pool, admin_pool, user_id=60010)
    ticket = await request_outcome_bytes(
        luck_pool, discord_id=60010, byte_count=64, game_context="t"
    )
    async with admin_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT server_seed FROM fairness.user_seeds WHERE discord_id = 60010"
        )
    server_seed = bytes(row["server_seed"])
    msg = f"{ticket.client_seed}:{ticket.nonce}".encode()
    expected = hmac.new(server_seed, msg, hashlib.sha512).digest()
    assert ticket.hmac_bytes == expected


async def test_ticket_byte_count_exact_match(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """Ticket carries exactly byte_count bytes — extended via SHA-256
    chain when byte_count > 64."""
    await _bootstrap_user(luck_pool, admin_pool, user_id=60011)
    for n in (16, 32, 64, 96, 128, 1244):
        t = await request_outcome_bytes(
            luck_pool, discord_id=60011, byte_count=n, game_context="t"
        )
        assert len(t.hmac_bytes) == n


async def test_ticket_first_64_bytes_match_engine_when_extended(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """When byte_count > 64, the first 64 bytes are the raw HMAC;
    the remaining bytes are SHA-256 chain extensions."""
    await _bootstrap_user(luck_pool, admin_pool, user_id=60012)
    ticket = await request_outcome_bytes(
        luck_pool, discord_id=60012, byte_count=128, game_context="t"
    )
    async with admin_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT server_seed FROM fairness.user_seeds WHERE discord_id = 60012"
        )
    server_seed = bytes(row["server_seed"])
    msg = f"{ticket.client_seed}:{ticket.nonce}".encode()
    head = hmac.new(server_seed, msg, hashlib.sha512).digest()
    assert ticket.hmac_bytes[:64] == head
    # The next 32 bytes are SHA-256(head || 1.to_bytes(4, 'big')).
    chunk1 = hashlib.sha256(head + (1).to_bytes(4, "big")).digest()
    assert ticket.hmac_bytes[64:96] == chunk1


# ---------------------------------------------------------------------------
# Concurrency — 100 parallel calls allocate 100 unique nonces
# ---------------------------------------------------------------------------


async def test_ticket_100_parallel_unique_nonces(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """100 parallel request_outcome_bytes for the same user must
    each get a unique nonce in [0, 99] — no duplicates, no skips.
    """
    await _bootstrap_user(luck_pool, admin_pool, user_id=60020)

    async def call_once() -> int:
        t = await request_outcome_bytes(
            luck_pool, discord_id=60020, byte_count=64, game_context="race"
        )
        return t.nonce

    nonces = await asyncio.gather(*(call_once() for _ in range(100)))
    assert sorted(nonces) == list(range(100))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_ticket_rejects_unknown_user(
    luck_pool: asyncpg.Pool,
) -> None:
    """A user with no fairness.user_seeds row gets a typed error.

    The caller is expected to have called ensure_seeds() before
    request_outcome_bytes; we surface the SDF's seed_not_found
    as a Python LookupError.
    """
    with pytest.raises(LookupError, match="seed_not_found"):
        await request_outcome_bytes(
            luck_pool,
            discord_id=99999998,
            byte_count=64,
            game_context="t",
        )


async def test_ticket_rejects_invalid_byte_count(
    luck_pool: asyncpg.Pool, admin_pool: asyncpg.Pool
) -> None:
    """byte_count must be > 0."""
    await _bootstrap_user(luck_pool, admin_pool, user_id=60030)
    with pytest.raises(ValueError, match="byte_count"):
        await request_outcome_bytes(
            luck_pool, discord_id=60030, byte_count=0, game_context="t"
        )
    with pytest.raises(ValueError, match="byte_count"):
        await request_outcome_bytes(
            luck_pool, discord_id=60030, byte_count=-1, game_context="t"
        )


# Module-hygiene assertions for api.py live in
# ``tests/unit/core/test_fairness_api_unit.py`` (sync, no DB).
