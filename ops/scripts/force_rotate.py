#!/usr/bin/env python3
"""Force-rotate one user's or all users' fairness seeds.

Spec ref: Luck design §4.3 ("operational rules").

Use this script to respond to a leak, a routine rotation, or any
admin-driven operation that rotates seeds outside the normal
``/rotateseed`` flow.

Each rotation:

1. Calls ``fairness.rotate_user_seed(user_id, 'admin')`` SDF.
   This archives the previous seed to ``fairness.history`` and
   generates a fresh server_seed + commitment hash.
2. Writes a ``core.audit_log`` row with ``actor_type='admin'``
   so the admin action is part of the platform-wide HMAC chain.
3. Logs to stdout: ``user_id`` + ``new server_seed_hash``.

Idempotency: re-running the script on the same user is harmless
— each call rotates again. There is no "no-op if recently
rotated" guard; that's the admin's responsibility.

Usage:

    POSTGRES_DSN=postgresql://deathroll_admin:...@host:5432/deathroll \\
    DEATHROLL_ADMIN_ACTOR_ID=<admin discord id> \\
    python ops/scripts/force_rotate.py --user 1234567890

    POSTGRES_DSN=postgresql://deathroll_admin:...@host:5432/deathroll \\
    DEATHROLL_ADMIN_ACTOR_ID=<admin discord id> \\
    python ops/scripts/force_rotate.py --all

The ``DEATHROLL_ADMIN_ACTOR_ID`` env var is the discord id of the
admin running the script — recorded as ``actor_id`` on every
audit row so the action is attributable.

Discord-side counterparts ``/admin force-rotate-seed`` and
``/admin force-rotate-all`` are stubbed in
``deathroll_luck/admin/force_rotate.py`` and wired into the bot
during Epic 11.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

_DEFAULT_REASON = "admin force-rotation"


async def _rotate_one(
    conn: asyncpg.Connection,
    *,
    target_id: int,
    admin_actor_id: int,
    reason: str,
) -> str:
    """Rotate a single user's seed + emit the audit row.

    Returns the new ``server_seed_hash`` as hex.
    """
    row = await conn.fetchrow(
        "SELECT * FROM fairness.rotate_user_seed("
        "  p_discord_id := $1, p_rotated_by := 'admin')",
        target_id,
    )
    assert row is not None
    new_hash = bytes(row["new_server_seed_hash"])

    # Get current balance for audit row's before/after (no balance
    # change but the chain helper expects them).
    bal = await conn.fetchval(
        "SELECT balance FROM core.balances WHERE discord_id = $1",
        target_id,
    )
    bal = int(bal) if bal is not None else 0

    await conn.fetchval(
        """
        SELECT core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := $1,
            p_target_id      := $2,
            p_action         := 'fairness_rotated',
            p_amount         := 0,
            p_balance_before := $3,
            p_balance_after  := $3,
            p_reason         := $4,
            p_ref_type       := 'fairness_rotation',
            p_ref_id         := $5,
            p_bot_name       := 'ops',
            p_metadata       := $6::jsonb
        )
        """,
        admin_actor_id,
        target_id,
        bal,
        reason,
        str(target_id),
        json.dumps({"new_server_seed_hash": new_hash.hex()}),
    )
    return new_hash.hex()


async def _all_user_ids(conn: asyncpg.Connection) -> list[int]:
    """Every user that has a fairness.user_seeds row, sorted."""
    rows = await conn.fetch(
        "SELECT discord_id FROM fairness.user_seeds ORDER BY discord_id"
    )
    return [int(r["discord_id"]) for r in rows]


async def _main_async(
    *, dsn: str, admin_actor_id: int, target_user: int | None, all_users: bool,
    reason: str,
) -> int:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    if pool is None:
        print("failed to create asyncpg pool", file=sys.stderr)
        return 2
    try:
        async with pool.acquire() as conn:
            if all_users:
                ids = await _all_user_ids(conn)
                print(
                    f"force-rotating {len(ids)} user(s) by admin "
                    f"{admin_actor_id}",
                    file=sys.stderr,
                )
                for uid in ids:
                    new_hash = await _rotate_one(
                        conn,
                        target_id=uid,
                        admin_actor_id=admin_actor_id,
                        reason=reason,
                    )
                    print(f"{uid} {new_hash}")
            else:
                assert target_user is not None
                new_hash = await _rotate_one(
                    conn,
                    target_id=target_user,
                    admin_actor_id=admin_actor_id,
                    reason=reason,
                )
                print(f"{target_user} {new_hash}")
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force-rotate one or all users' fairness seeds."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--user",
        type=int,
        metavar="DISCORD_ID",
        help="Rotate this user's seed only.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Rotate every user with an existing fairness.user_seeds row.",
    )
    parser.add_argument(
        "--reason",
        default=_DEFAULT_REASON,
        help="Free-text reason recorded on the audit row.",
    )
    args = parser.parse_args()

    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        print(
            "POSTGRES_DSN env var must be set "
            "(postgresql://deathroll_admin:...@host:5432/deathroll)",
            file=sys.stderr,
        )
        return 2

    admin_actor_id_str = os.environ.get("DEATHROLL_ADMIN_ACTOR_ID")
    if not admin_actor_id_str:
        print(
            "DEATHROLL_ADMIN_ACTOR_ID env var must be set "
            "(the Discord id of the admin running the script)",
            file=sys.stderr,
        )
        return 2
    try:
        admin_actor_id = int(admin_actor_id_str)
    except ValueError:
        print(
            f"DEATHROLL_ADMIN_ACTOR_ID must be an integer; got "
            f"{admin_actor_id_str!r}",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(
        _main_async(
            dsn=dsn,
            admin_actor_id=admin_actor_id,
            target_user=args.user,
            all_users=args.all,
            reason=args.reason,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
