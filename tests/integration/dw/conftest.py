"""Pytest fixtures for the D/W integration suite (Epic 14).

A session-scoped Postgres container, populated once with:

1. The four per-bot roles (``goldrush_admin`` already exists from the
   container init; we add ``goldrush_dw``, ``goldrush_luck``,
   ``goldrush_poker``, ``goldrush_readonly``).
2. ``ops/postgres/01-schemas-grants.sql`` — schema creation + privilege
   matrix that the alembic migrations rely on.
3. ``app.audit_chain_key`` set as a database GUC so
   ``core.audit_log_insert_with_chain`` can compute HMAC chains.
4. ``alembic upgrade head`` (revisions 0001 … 0018).

Per-test isolation: each test gets a fresh asyncpg pool; before
yielding, every dw.* + core.* row is wiped (via TRUNCATE which
bypasses the audit_log append-only triggers) and sequences reset.
The ``core.balances`` treasury seed row (discord_id=0) and the
``dw.global_config`` seed (migration 0005) are re-inserted.

Marker: every test in this directory is implicitly marked
``integration`` via :func:`pytest_collection_modifyitems` so the
unit suite (``pytest -m "not integration"``) still runs in <5 s.

Skip behaviour: if Docker isn't reachable on the host running the
tests, every integration test is auto-skipped with a clear reason.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

# testcontainers is in the dev deps but its imports trigger Docker
# probing on import — guard so a missing docker daemon doesn't crash
# pytest collection.
try:
    from testcontainers.postgres import PostgresContainer

    _TESTCONTAINERS_AVAILABLE = True
except Exception:  # pragma: no cover — fallback diagnostics only
    PostgresContainer = None  # type: ignore[assignment,misc]
    _TESTCONTAINERS_AVAILABLE = False


_REPO_ROOT = Path(__file__).resolve().parents[3]
_INIT_ROLES = _REPO_ROOT / "ops" / "postgres" / "00-init-roles.sh"
_SCHEMAS_GRANTS = _REPO_ROOT / "ops" / "postgres" / "01-schemas-grants.sql"
_ALEMBIC_DIR = _REPO_ROOT / "ops" / "alembic"

# A 32-byte hex-encoded value is what the SDFs expect (decode('hex') in
# audit_log_insert_with_chain). Generated once per session.
_CHAIN_KEY = secrets.token_hex(32)

# Test passwords. These are NOT used outside the container — generated
# fresh per session and only ever travel via DSNs to the same container.
_DW_PW = secrets.token_urlsafe(20)
_LUCK_PW = secrets.token_urlsafe(20)
# Production compose lets ``goldrush_poker`` be skipped (the bot is on
# hold) but the schemas+grants SQL references it unconditionally — so
# in tests we always create the role.
_POKER_PW = secrets.token_urlsafe(20)
_RO_PW = secrets.token_urlsafe(20)


def _docker_available() -> bool:
    """Best-effort probe; cheap so we run it once per session."""
    if not _TESTCONTAINERS_AVAILABLE:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-mark every test in this directory as ``integration`` and
    skip them if Docker is unreachable. The unit suite (``pytest -m
    'not integration'``) is unaffected."""
    integration_root = Path(__file__).parent
    docker_ok = _docker_available()
    skip_reason = "Docker daemon not reachable; skipping integration tests"
    for item in items:
        if integration_root in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)
            if not docker_ok:
                item.add_marker(pytest.mark.skip(reason=skip_reason))


# ---------------------------------------------------------------------------
# Session-scoped Postgres container.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[Any]:
    """Spin up a fresh Postgres 16 container, populate it with the
    bot's roles + schemas + migrations, return the running container."""
    if PostgresContainer is None:
        pytest.skip("testcontainers not installed")
    if not _docker_available():
        pytest.skip("Docker daemon not reachable")

    # Vanilla postgres container; we run the role / grant SQL ourselves
    # via psycopg2 instead of mounting the project's init scripts. This
    # sidesteps Windows CRLF issues with mounted shell scripts and keeps
    # the fixture's behaviour explicit.
    container = PostgresContainer(
        "postgres:16-alpine",
        username="goldrush_admin",
        password="admin_test_pw",
        dbname="goldrush",
    )

    container.start()
    try:
        admin_dsn = container.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql"
        )
        _bootstrap_db(admin_dsn=admin_dsn)
        _run_alembic(admin_dsn=admin_dsn)
        yield container
    finally:
        container.stop()


def _bootstrap_db(*, admin_dsn: str) -> None:
    """Create the per-bot roles + run the schemas/grants SQL.

    Replaces the ``00-init-roles.sh`` + ``01-schemas-grants.sql`` pair
    that the production compose mounts into the postgres container —
    we apply equivalent SQL directly so the fixture is self-contained
    and independent of file-mount semantics on the test host.
    """
    import psycopg2  # type: ignore[import-untyped]

    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for role, pw in (
                ("goldrush_dw", _DW_PW),
                ("goldrush_luck", _LUCK_PW),
                ("goldrush_poker", _POKER_PW),
                ("goldrush_readonly", _RO_PW),
            ):
                # Idempotent CREATE — same shape as 00-init-roles.sh.
                cur.execute(
                    "DO $$ BEGIN "
                    f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                    f"EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '{role}', '{pw}'); "
                    "ELSE "
                    f"EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '{role}', '{pw}'); "
                    "END IF; END $$"
                )

            # Apply schemas + grants from the canonical SQL file.
            schemas_sql = _SCHEMAS_GRANTS.read_text(encoding="utf-8")
            cur.execute(schemas_sql)

            # Set the audit chain key as a database GUC so the SDFs
            # can read it via current_setting('app.audit_chain_key').
            cur.execute(
                f"ALTER DATABASE goldrush SET app.audit_chain_key = '{_CHAIN_KEY}'"
            )
    finally:
        conn.close()


def _run_alembic(*, admin_dsn: str) -> None:
    """Execute ``alembic upgrade head`` against the test database."""
    env = os.environ.copy()
    env["POSTGRES_DSN_ADMIN"] = admin_dsn
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Per-test pool with TRUNCATE-on-yield isolation.
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Async asyncpg pool connected as ``goldrush_dw`` (the bot's
    role). Tables are TRUNCATEd before yield so each test sees a
    clean slate; the treasury seed row + dw.global_config defaults
    are re-inserted using the admin connection.
    """
    admin_dsn = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )
    # Build the bot DSN by swapping the admin role + pw in the URL.
    # testcontainers gives us admin credentials in the DSN already.
    bot_dsn = admin_dsn.replace("goldrush_admin", "goldrush_dw").replace(
        "admin_test_pw", _DW_PW
    )

    # Wipe + reseed via admin connection so the bot's role can run
    # the test ops on a known baseline.
    await _reset_db(admin_dsn=admin_dsn)

    bot_pool = await asyncpg.create_pool(bot_dsn, min_size=1, max_size=8)
    assert bot_pool is not None
    try:
        yield bot_pool
    finally:
        await bot_pool.close()


@pytest.fixture
async def admin_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Admin-role pool — needed for tests that exercise privileges
    (e.g. trigger-level immutability checks) where the bot role
    can't even attempt a forbidden action."""
    admin_dsn = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )
    pool = await asyncpg.create_pool(admin_dsn, min_size=1, max_size=4)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _reset_db(*, admin_dsn: str) -> None:
    """TRUNCATE every dw.* + core.* table (audit_log triggers don't fire
    on TRUNCATE) and reseed the treasury balance + dw.global_config
    defaults so the test sees a fresh, valid state."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        # Order matters for TRUNCATE CASCADE — listing all tables
        # together lets Postgres figure it out.
        await conn.execute(
            """
            TRUNCATE
                core.audit_log,
                core.audit_chain_state,
                core.balances,
                core.users,
                dw.deposit_tickets,
                dw.withdraw_tickets,
                dw.cashier_status,
                dw.cashier_sessions,
                dw.cashier_characters,
                dw.cashier_stats,
                dw.disputes,
                dw.dynamic_embeds,
                dw.global_config
            RESTART IDENTITY CASCADE
            """
        )
        # Re-seed the chain state row (PK guard ensures id=1).
        await conn.execute(
            "INSERT INTO core.audit_chain_state (id, last_row_hash) VALUES (1, NULL)"
        )
        # Re-seed dw.global_config (mirrors migration 0005's defaults).
        await conn.execute(
            """
            INSERT INTO dw.global_config (key, value_int, updated_by) VALUES
                ('min_deposit_g',          200,    0),
                ('max_deposit_g',          200000, 0),
                ('min_withdraw_g',         1000,   0),
                ('max_withdraw_g',         200000, 0),
                ('withdraw_fee_bps',       200,    0),
                ('deposit_fee_bps',        0,      0),
                ('daily_user_limit_g',     0,      0),
                ('ticket_expiry_open_s',   86400,  0),
                ('ticket_repinging_s',     3600,   0),
                ('ticket_claim_idle_s',    1800,   0),
                ('ticket_claim_expiry_s',  7200,   0),
                ('cashier_auto_offline_s', 3600,   0)
            """
        )
        # Treasury seed row — discord_id=0 represents the bot's
        # accounting bucket and must exist before any treasury op.
        # core.users requires a row before core.balances FK is
        # satisfied; create both.
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (0) ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO core.balances (discord_id, balance) VALUES (0, 0) "
            "ON CONFLICT DO NOTHING"
        )
    finally:
        await conn.close()
