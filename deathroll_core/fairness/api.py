"""Fairness API — the single async entry point every game cog calls.

Spec ref: Luck design §4.6, §4.4.

``request_outcome_bytes`` orchestrates the per-bet randomness
draw atomically:

1. Open a transaction on the pool.
2. ``SELECT ... FOR UPDATE`` on the user's ``fairness.user_seeds``
   row to lock it against concurrent ``rotate_user_seed``.
3. Call ``fairness.next_nonce(discord_id)`` SDF inside the
   transaction so the nonce increment is atomic with the read.
4. Read the raw ``server_seed`` (sensitive — never logged, never
   returned outside this function).
5. Compute ``HMAC-SHA512(server_seed, f"{client_seed}:{nonce}")``
   via :func:`deathroll_core.fairness.engine.compute`.
6. If ``byte_count > 64``, extend with the same SHA-256 chain
   the decoders use (see ``decoders._byte_stream``).
7. Return a :class:`FairnessTicket` carrying ONLY the public state
   plus the ``hmac_bytes`` — the raw ``server_seed`` never escapes
   the function.

The ``FairnessTicket`` is what each game's resolver consumes; it
plus the ``selection`` is enough to fully derive the outcome.
"""

from __future__ import annotations

import hashlib

import asyncpg
from pydantic import BaseModel, ConfigDict

from deathroll_core.fairness.engine import compute


class FairnessTicket(BaseModel):
    """A single bet's randomness draw + the public seed state at
    that moment.

    Carries:

    - ``hmac_bytes``: the ``HMAC-SHA512`` output (≥ 64 bytes;
      possibly extended via SHA-256 chain).
    - ``server_seed_hash``: the public commitment as it was at
      the time of the draw (for the user to record alongside
      their bet).
    - ``client_seed``: the client_seed used in the HMAC message.
    - ``nonce``: the per-user counter value used in the HMAC
      message; one-to-one with the bet.

    The raw ``server_seed`` is structurally not on this model.
    Pinned by ``test_ticket_carries_only_public_fields``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    hmac_bytes: bytes
    server_seed_hash: bytes
    client_seed: str
    nonce: int


def _extend(out: bytes, byte_count: int) -> bytes:
    """If ``byte_count`` > len(``out``), extend deterministically
    via SHA-256 chain so callers can request more than 64 bytes
    of randomness for one bet (e.g., Blackjack's 312-card shoe).

    The chain matches ``decoders._byte_stream`` exactly so the
    same bytes feed into both ``api.request_outcome_bytes`` and
    the per-game decoders.
    """
    if byte_count <= len(out):
        return out[:byte_count]
    extended = bytearray(out)
    counter = 0
    while len(extended) < byte_count:
        counter += 1
        chunk = hashlib.sha256(out + counter.to_bytes(4, "big")).digest()
        extended.extend(chunk)
    return bytes(extended[:byte_count])


async def request_outcome_bytes(
    pool: asyncpg.Pool,
    *,
    discord_id: int,
    byte_count: int,
    game_context: str,
) -> FairnessTicket:
    """Atomically allocate the next nonce and draw ``byte_count``
    bytes of HMAC-SHA512 randomness for the user.

    Args:
        pool: asyncpg pool connected as ``deathroll_luck``.
        discord_id: target user.
        byte_count: number of random bytes the caller needs. Most
            games need ≤ 64 (one HMAC); Blackjack's 312-card shoe
            needs ~1244 bytes (extended via SHA-256 chain).
        game_context: opaque label for observability (e.g.,
            "coinflip", "blackjack-hand-2"). NOT part of the
            randomness derivation; just for downstream logs.

    Returns:
        A :class:`FairnessTicket` carrying the bytes + the public
        seed state at the time of the draw.

    Raises:
        ValueError: if ``byte_count <= 0``.
        LookupError: if the user has no ``fairness.user_seeds`` row
            (caller must have invoked ``ensure_seeds`` first).
    """
    if byte_count <= 0:
        raise ValueError(f"byte_count must be > 0; got {byte_count}")
    # game_context is currently observability-only; reserved
    # parameter for future audit / metric labelling.
    _ = game_context

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the user's row so no rotate_user_seed slips
            # between the next_nonce call and the seed read.
            locked = await conn.fetchrow(
                "SELECT 1 FROM fairness.user_seeds "
                "WHERE discord_id = $1 FOR UPDATE",
                discord_id,
            )
            if locked is None:
                raise LookupError(f"seed_not_found: {discord_id}")
            used_nonce = await conn.fetchval(
                "SELECT fairness.next_nonce(p_discord_id := $1)",
                discord_id,
            )
            row = await conn.fetchrow(
                "SELECT server_seed, server_seed_hash, client_seed "
                "FROM fairness.user_seeds WHERE discord_id = $1",
                discord_id,
            )

    assert row is not None  # LOCK confirmed it's there
    raw_seed = bytes(row["server_seed"])
    public_hash = bytes(row["server_seed_hash"])
    client_seed: str = row["client_seed"]
    nonce: int = int(used_nonce)

    head = compute(raw_seed, client_seed, nonce)
    bytes_out = _extend(head, byte_count)
    # raw_seed goes out of scope here — never embedded in the
    # FairnessTicket.

    return FairnessTicket(
        hmac_bytes=bytes_out,
        server_seed_hash=public_hash,
        client_seed=client_seed,
        nonce=nonce,
    )
