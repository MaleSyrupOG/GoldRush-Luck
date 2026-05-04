"""Story 3.6 — force-rotate admin tooling.

Exercises ``ops/scripts/force_rotate.py`` against testcontainers
Postgres. Verifies:

- ``--user <id>`` rotates the named user only.
- ``--all`` rotates every user with a user_seeds row.
- Each rotation writes an audit row with ``actor_type='admin'``.
- The script is idempotent (running twice on the same user
  rotates twice; audit log gets two entries).
- Stdout reports ``<user_id> <new_server_seed_hash_hex>`` per
  rotation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from deathroll_core.fairness.seeds import ensure_seeds

pytestmark = pytest.mark.asyncio


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORCE_ROTATE = _REPO_ROOT / "ops" / "scripts" / "force_rotate.py"


def _admin_dsn(postgres_container) -> str:
    return postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )


def _run_script(
    *args: str, dsn: str, admin_actor_id: int = 1234
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["POSTGRES_DSN"] = dsn
    env["DEATHROLL_ADMIN_ACTOR_ID"] = str(admin_actor_id)
    return subprocess.run(
        [sys.executable, str(_FORCE_ROTATE), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# --user <id>
# ---------------------------------------------------------------------------


async def test_force_rotate_one_user(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """--user <id> rotates exactly one user; the previous hash is
    archived to fairness.history."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (70001) "
            "ON CONFLICT DO NOTHING"
        )
    state = await ensure_seeds(luck_pool, discord_id=70001)
    prior_hash = state.server_seed_hash

    res = _run_script("--user", "70001", dsn=_admin_dsn(postgres_container))
    assert res.returncode == 0, res.stderr
    # stdout: '70001 <new_hash>'
    parts = res.stdout.strip().split()
    assert parts[0] == "70001"
    new_hash = bytes.fromhex(parts[1])

    async with admin_pool.acquire() as conn:
        # The previous hash now in history.
        history = await conn.fetchrow(
            "SELECT server_seed_hash FROM fairness.history "
            "WHERE discord_id = 70001 ORDER BY id DESC LIMIT 1"
        )
        # The user_seeds row carries the new hash.
        live = await conn.fetchrow(
            "SELECT server_seed_hash FROM fairness.user_seeds WHERE discord_id = 70001"
        )
    assert bytes(history["server_seed_hash"]) == prior_hash
    assert bytes(live["server_seed_hash"]) == new_hash


async def test_force_rotate_writes_audit_row(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (70002) "
            "ON CONFLICT DO NOTHING"
        )
    await ensure_seeds(luck_pool, discord_id=70002)

    res = _run_script(
        "--user",
        "70002",
        "--reason",
        "post-leak rotation",
        dsn=_admin_dsn(postgres_container),
        admin_actor_id=999,
    )
    assert res.returncode == 0, res.stderr

    async with admin_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT actor_type, actor_id, target_id, action, reason "
            "FROM core.audit_log "
            "WHERE action = 'fairness_rotated' AND target_id = 70002 "
            "ORDER BY id DESC LIMIT 1"
        )
    assert row is not None
    assert row["actor_type"] == "admin"
    assert row["actor_id"] == 999
    assert row["target_id"] == 70002
    assert row["action"] == "fairness_rotated"
    assert row["reason"] == "post-leak rotation"


async def test_force_rotate_audit_metadata_carries_new_hash(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """The audit row's metadata carries the new server_seed_hash —
    so a future audit can confirm the rotation actually happened."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (70003) "
            "ON CONFLICT DO NOTHING"
        )
    await ensure_seeds(luck_pool, discord_id=70003)

    res = _run_script(
        "--user", "70003", dsn=_admin_dsn(postgres_container)
    )
    assert res.returncode == 0, res.stderr

    async with admin_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT metadata, server_seed_hash AS live_hash "
            "FROM core.audit_log al, fairness.user_seeds us "
            "WHERE al.action = 'fairness_rotated' "
            "  AND al.target_id = 70003 "
            "  AND us.discord_id = 70003 "
            "ORDER BY al.id DESC LIMIT 1"
        )
    import json

    metadata = json.loads(row["metadata"])
    assert "new_server_seed_hash" in metadata
    assert (
        bytes.fromhex(metadata["new_server_seed_hash"])
        == bytes(row["live_hash"])
    )


# ---------------------------------------------------------------------------
# --all
# ---------------------------------------------------------------------------


async def test_force_rotate_all(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """--all rotates every user with a user_seeds row; emits one
    audit_log row per rotation."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES "
            "  (70010), (70011), (70012) ON CONFLICT DO NOTHING"
        )
    for uid in (70010, 70011, 70012):
        await ensure_seeds(luck_pool, discord_id=uid)

    res = _run_script("--all", dsn=_admin_dsn(postgres_container))
    assert res.returncode == 0, res.stderr
    # 3 lines of stdout — one per user.
    out_lines = res.stdout.strip().splitlines()
    assert len(out_lines) == 3
    out_uids = sorted(int(line.split()[0]) for line in out_lines)
    assert out_uids == [70010, 70011, 70012]

    async with admin_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM core.audit_log "
            "WHERE action = 'fairness_rotated' "
            "  AND target_id IN (70010, 70011, 70012)"
        )
    assert cnt == 3


# ---------------------------------------------------------------------------
# Idempotency on user level
# ---------------------------------------------------------------------------


async def test_force_rotate_twice_in_a_row(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """Rotating twice in a row is allowed; each rotation creates a
    fresh history row + a fresh audit row.

    Per spec AC: idempotent on user level (rotating twice in a row
    is allowed)."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (70020) "
            "ON CONFLICT DO NOTHING"
        )
    await ensure_seeds(luck_pool, discord_id=70020)

    r1 = _run_script("--user", "70020", dsn=_admin_dsn(postgres_container))
    assert r1.returncode == 0, r1.stderr
    r2 = _run_script("--user", "70020", dsn=_admin_dsn(postgres_container))
    assert r2.returncode == 0, r2.stderr

    async with admin_pool.acquire() as conn:
        history_cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM fairness.history WHERE discord_id = 70020"
        )
        audit_cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM core.audit_log "
            "WHERE action = 'fairness_rotated' AND target_id = 70020"
        )
    assert history_cnt == 2  # bootstrap-archive (none) + r1 + r2 = 2 archives
    assert audit_cnt == 2


# ---------------------------------------------------------------------------
# Hash commitment held end-to-end
# ---------------------------------------------------------------------------


async def test_force_rotate_revealed_seed_satisfies_commitment(
    postgres_container,
    luck_pool: asyncpg.Pool,
    admin_pool: asyncpg.Pool,
) -> None:
    """After a force-rotate, the revealed_server_seed in fairness.history
    satisfies SHA-256(revealed) == prior_hash — the commit-reveal model
    holds across admin-driven rotations too."""
    async with admin_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (70030) "
            "ON CONFLICT DO NOTHING"
        )
    await ensure_seeds(luck_pool, discord_id=70030)
    async with admin_pool.acquire() as conn:
        prior = await conn.fetchrow(
            "SELECT server_seed_hash FROM fairness.user_seeds "
            "WHERE discord_id = 70030"
        )
    prior_hash = bytes(prior["server_seed_hash"])

    res = _run_script("--user", "70030", dsn=_admin_dsn(postgres_container))
    assert res.returncode == 0, res.stderr

    async with admin_pool.acquire() as conn:
        history = await conn.fetchrow(
            "SELECT revealed_server_seed FROM fairness.history "
            "WHERE discord_id = 70030 ORDER BY id DESC LIMIT 1"
        )
    revealed = bytes(history["revealed_server_seed"])
    assert hashlib.sha256(revealed).digest() == prior_hash


# ---------------------------------------------------------------------------
# CLI argument validation
# ---------------------------------------------------------------------------


async def test_force_rotate_requires_user_or_all(
    postgres_container,
) -> None:
    res = _run_script(dsn=_admin_dsn(postgres_container))
    assert res.returncode == 2  # argparse exit code
    assert (
        "one of the arguments" in res.stderr
        or "required" in res.stderr.lower()
    )


async def test_force_rotate_requires_admin_actor_env(
    postgres_container,
) -> None:
    """Without DEATHROLL_ADMIN_ACTOR_ID, the script aborts."""
    env = os.environ.copy()
    env["POSTGRES_DSN"] = _admin_dsn(postgres_container)
    env.pop("DEATHROLL_ADMIN_ACTOR_ID", None)
    res = subprocess.run(
        [sys.executable, str(_FORCE_ROTATE), "--user", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 2
    assert "DEATHROLL_ADMIN_ACTOR_ID" in res.stderr
