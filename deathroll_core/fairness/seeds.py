"""Per-user seed lifecycle wrapper.

Spec ref: Luck design §4.2, §4.3.

Thin async wrapper around the ``fairness.user_seeds`` table and
the ``fairness.rotate_user_seed`` SECURITY DEFINER fn (Story
2.8d). The Python surface returns a public-only :class:`SeedState`
that NEVER carries the raw ``server_seed`` — that field is not
on the model. The bot's logging machinery therefore cannot
accidentally expose the secret via any structlog / logger /
print path.

The four public functions:

- :func:`ensure_seeds` — idempotent bootstrap. First call creates
  the user_seeds row via ``rotate_user_seed`` (with
  ``rotated_by='system'``); subsequent calls just return the
  existing public state.

- :func:`get_public_state` — read-only fetch. Returns ``None`` if
  the user has no row yet.

- :func:`set_client_seed` — validates the new client_seed against
  the canonical regex (``^[A-Za-z0-9_\\-]{1,64}$``) and updates
  the row. Does NOT reset the nonce (per spec §4.2).

- :func:`rotate` — calls the ``fairness.rotate_user_seed`` SDF.
  Returns the revealed previous server_seed (or ``None`` on first
  rotation) and the new public state.

Operational rule (audit constraint): no log line in this module
mentions the literal ``server_seed`` variable name. Pinned in
``tests/unit/core/test_fairness_seeds_unit.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncpg
from pydantic import BaseModel, ConfigDict

# The canonical client_seed regex. Documented in spec §4.2; pinned
# by ``test_client_seed_regex_pinned``.
CLIENT_SEED_REGEX = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def validate_client_seed(value: str) -> str:
    """Return ``value`` if it matches :data:`CLIENT_SEED_REGEX`,
    else raise :class:`ValueError`.

    Spec §4.2 — only ``[A-Za-z0-9_-]`` characters, length 1-64.
    """
    if not isinstance(value, str) or not CLIENT_SEED_REGEX.fullmatch(value):
        raise ValueError(f"invalid client_seed: {value!r}")
    return value


class SeedState(BaseModel):
    """Public state of a user's fairness seed.

    Carries only the three publicly-visible fields:
    ``server_seed_hash`` (commitment), ``client_seed`` (user-editable),
    ``nonce`` (monotonic counter). The raw ``server_seed`` is NOT
    on this model — by structural design — so no codepath can
    accidentally expose it via logging, repr, or JSON
    serialisation.

    ``extra='forbid'`` rejects any attempt to construct a
    SeedState with a smuggled ``server_seed`` field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    server_seed_hash: bytes
    client_seed: str
    nonce: int


@dataclass(frozen=True, slots=True)
class RotateResult:
    """Return value of :func:`rotate`.

    ``revealed_server_seed`` is the OLD server_seed that the user
    can now use to verify any past bet retrospectively. It is
    ``None`` on the first rotation (when there is no prior to
    archive). After this function returns, callers MUST treat
    the revealed seed as part of the bet's verification artefact
    — DO NOT log it, DO NOT echo it into Discord embeds beyond
    the user's own ephemeral confirmation.

    ``new_state`` carries the new public commitment + reset nonce.
    """

    revealed_server_seed: bytes | None
    new_state: SeedState


# ---------------------------------------------------------------------------
# ensure_seeds
# ---------------------------------------------------------------------------


async def ensure_seeds(
    pool: asyncpg.Pool, *, discord_id: int
) -> SeedState:
    """Idempotent bootstrap.

    First call: creates a new user_seeds row via
    ``fairness.rotate_user_seed(discord_id, 'system')``. The row
    starts at ``nonce=0`` with a fresh CSPRNG-generated
    ``server_seed`` and a default 16-char hex ``client_seed``.

    Subsequent calls: noop. Returns the existing public state
    without rotating or modifying anything.

    Returns:
        The (possibly newly-created) public state.
    """
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = $1",
            discord_id,
        )
        if existing is not None:
            return SeedState(
                server_seed_hash=bytes(existing["server_seed_hash"]),
                client_seed=existing["client_seed"],
                nonce=existing["nonce"],
            )
        # Bootstrap via SDF.
        await conn.fetchrow(
            "SELECT * FROM fairness.rotate_user_seed("
            "  p_discord_id := $1, p_rotated_by := 'system')",
            discord_id,
        )
        # Re-read; SDF returns the new hash but not the full state.
        row = await conn.fetchrow(
            "SELECT server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = $1",
            discord_id,
        )
        assert row is not None  # SDF just inserted it
        return SeedState(
            server_seed_hash=bytes(row["server_seed_hash"]),
            client_seed=row["client_seed"],
            nonce=row["nonce"],
        )


# ---------------------------------------------------------------------------
# get_public_state
# ---------------------------------------------------------------------------


async def get_public_state(
    pool: asyncpg.Pool, *, discord_id: int
) -> SeedState | None:
    """Read-only fetch of the user's public seed state.

    Returns ``None`` if the user has no row yet (caller can
    invoke :func:`ensure_seeds` to bootstrap).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = $1",
            discord_id,
        )
    if row is None:
        return None
    return SeedState(
        server_seed_hash=bytes(row["server_seed_hash"]),
        client_seed=row["client_seed"],
        nonce=row["nonce"],
    )


# ---------------------------------------------------------------------------
# set_client_seed
# ---------------------------------------------------------------------------


async def set_client_seed(
    pool: asyncpg.Pool, *, discord_id: int, new_client_seed: str
) -> SeedState:
    """Validate + UPDATE the user's client_seed.

    Per spec §4.2, this does NOT reset the nonce — the user keeps
    advancing through the sequence; only the suffix of the HMAC
    message changes.

    Raises:
        ValueError: if ``new_client_seed`` violates the canonical
            regex (see :data:`CLIENT_SEED_REGEX`).
        LookupError: if the user has no user_seeds row (caller
            must call :func:`ensure_seeds` first).
    """
    validated = validate_client_seed(new_client_seed)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE fairness.user_seeds "
            "SET client_seed = $2 "
            "WHERE discord_id = $1 "
            "RETURNING server_seed_hash, client_seed, nonce",
            discord_id,
            validated,
        )
    if row is None:
        raise LookupError(f"seed_not_found: {discord_id}")
    return SeedState(
        server_seed_hash=bytes(row["server_seed_hash"]),
        client_seed=row["client_seed"],
        nonce=row["nonce"],
    )


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


async def rotate(
    pool: asyncpg.Pool, *, discord_id: int, rotated_by: str
) -> RotateResult:
    """Rotate the user's seed via ``fairness.rotate_user_seed``.

    On first rotation, returns ``RotateResult(revealed=None,
    new_state=...)``. On subsequent rotations, returns the
    previously-committed ``server_seed`` as ``revealed_server_seed``
    so the user can verify any past bet retrospectively.

    Args:
        pool: asyncpg pool connected as ``deathroll_luck`` or
            ``deathroll_dw`` (both have EXECUTE on the SDF).
        discord_id: target user's Discord id.
        rotated_by: ``'user'`` (user-driven), ``'system'`` (auto
            bootstrap), or ``'admin'`` (force-rotate, Story 3.6).

    Raises:
        ValueError: if ``rotated_by`` is not one of the three
            allowed values (the SDF raises ``invalid_rotated_by``).
    """
    if rotated_by not in ("user", "system", "admin"):
        # Mirror the SDF's named-exception so callers get a clean
        # ValueError without the SQL noise.
        raise ValueError(f"invalid_rotated_by: {rotated_by}")

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT * FROM fairness.rotate_user_seed("
                "  p_discord_id := $1, p_rotated_by := $2)",
                discord_id,
                rotated_by,
            )
        except asyncpg.exceptions.RaiseError as exc:
            # Surface invalid_rotated_by from the SDF as a Python
            # ValueError too (defence-in-depth).
            if "invalid_rotated_by" in str(exc):
                raise ValueError(f"invalid_rotated_by: {rotated_by}") from exc
            raise
        assert row is not None
        revealed_raw = row["revealed_server_seed"]
        # Re-read the state to get all three public fields.
        state_row = await conn.fetchrow(
            "SELECT server_seed_hash, client_seed, nonce "
            "FROM fairness.user_seeds WHERE discord_id = $1",
            discord_id,
        )
        assert state_row is not None

    new_state = SeedState(
        server_seed_hash=bytes(state_row["server_seed_hash"]),
        client_seed=state_row["client_seed"],
        nonce=state_row["nonce"],
    )
    revealed = bytes(revealed_raw) if revealed_raw is not None else None
    return RotateResult(revealed_server_seed=revealed, new_state=new_state)
