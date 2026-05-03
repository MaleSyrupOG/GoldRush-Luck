"""Pytest fixtures for the Luck integration suite.

Mirrors the D/W conftest pattern (``tests/integration/dw/conftest.py``):
session-scoped Postgres container populated once with roles + schemas
+ alembic head; per-test asyncpg pool connecting as ``deathroll_luck``;
per-test TRUNCATE+reseed for isolation.

Two containers per pytest run (one for D/W tests, one for Luck tests)
is the v1 cost of keeping the conftests independent. A future refactor
can pull the shared bootstrap into ``tests/integration/conftest.py`` so
both suites share a single container; that refactor lands in its own
commit so it doesn't ride alongside the Luck schema work.

Marker: every test in this directory is implicitly marked
``integration`` via :func:`pytest_collection_modifyitems` so the unit
suite (``pytest -m "not integration"``) still runs in <5 s.

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

    container = PostgresContainer(
        "postgres:16-alpine",
        username="deathroll_admin",
        password="admin_test_pw",
        dbname="deathroll",
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
                ("deathroll_dw", _DW_PW),
                ("deathroll_luck", _LUCK_PW),
                ("deathroll_poker", _POKER_PW),
                ("deathroll_readonly", _RO_PW),
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
                f"ALTER DATABASE deathroll SET app.audit_chain_key = '{_CHAIN_KEY}'"
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
async def luck_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Async asyncpg pool connected as ``deathroll_luck`` (the bot's
    role for Luck operations). Tables are TRUNCATEd before yield so
    each test sees a clean slate; shared seeds are re-inserted using
    the admin connection.
    """
    admin_dsn = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )
    bot_dsn = admin_dsn.replace("deathroll_admin", "deathroll_luck").replace(
        "admin_test_pw", _LUCK_PW
    )

    await _reset_db(admin_dsn=admin_dsn)

    bot_pool = await asyncpg.create_pool(bot_dsn, min_size=1, max_size=8)
    assert bot_pool is not None
    try:
        yield bot_pool
    finally:
        await bot_pool.close()


@pytest.fixture
async def dw_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Async asyncpg pool connected as ``deathroll_dw``.

    Used by tests that verify the cross-bot privilege story: e.g.
    that D/W can also INSERT into ``fairness.*`` tables (because
    seed rotation may bind to the withdraw cycle in v1.x).
    """
    admin_dsn = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )
    bot_dsn = admin_dsn.replace("deathroll_admin", "deathroll_dw").replace(
        "admin_test_pw", _DW_PW
    )
    pool = await asyncpg.create_pool(bot_dsn, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


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


@pytest.fixture
async def readonly_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Pool connected as ``deathroll_readonly`` for tests that verify
    SELECT-only privileges hold (i.e. that this role cannot mutate
    the schemas it can read)."""
    admin_dsn = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql"
    )
    ro_dsn = admin_dsn.replace("deathroll_admin", "deathroll_readonly").replace(
        "admin_test_pw", _RO_PW
    )
    pool = await asyncpg.create_pool(ro_dsn, min_size=1, max_size=4)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _reset_db(*, admin_dsn: str) -> None:
    """TRUNCATE every Luck-relevant table (audit_log triggers don't
    fire on TRUNCATE) and reseed shared state so the test sees a
    fresh, valid state.

    The list grows as more Luck migrations land. Tables not yet
    created are guarded with ``IF EXISTS``-style discovery — we
    introspect ``information_schema.tables`` and TRUNCATE only the
    ones that exist, making the conftest forward-compatible with
    in-progress migrations.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        # Discover which Luck-relevant tables actually exist on this
        # head. Lets the conftest survive partial Luck schema work
        # without needing to be edited per migration.
        candidates = [
            ("core", "audit_log"),
            ("core", "audit_chain_state"),
            ("core", "balances"),
            ("core", "users"),
            ("fairness", "user_seeds"),
            ("fairness", "history"),
            ("luck", "bet_rounds"),
            ("luck", "bets"),
            ("luck", "channel_binding"),
            ("luck", "game_config"),
            ("luck", "game_sessions"),
            ("luck", "global_config"),
            ("luck", "leaderboard_snapshot"),
            ("luck", "raffle_draws"),
            ("luck", "raffle_periods"),
            ("luck", "raffle_tickets"),
            ("luck", "rate_limit_entries"),
        ]
        existing = []
        for schema, table in candidates:
            row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = $1 AND table_name = $2",
                schema,
                table,
            )
            if row is not None:
                existing.append(f"{schema}.{table}")

        if existing:
            await conn.execute(
                f"TRUNCATE {', '.join(existing)} RESTART IDENTITY CASCADE"
            )

        # Re-seed the chain state row (PK guard ensures id=1).
        await conn.execute(
            "INSERT INTO core.audit_chain_state (id, last_row_hash) VALUES (1, NULL)"
        )

        # Treasury seed row — discord_id=0 represents the bot's
        # accounting bucket and must exist before any Luck SDF that
        # routes commission/rake into it.
        await conn.execute(
            "INSERT INTO core.users (discord_id) VALUES (0) ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO core.balances (discord_id, balance) VALUES (0, 0) "
            "ON CONFLICT DO NOTHING"
        )

        # Re-seed luck.game_config + luck.global_config defaults
        # (mirrors migration 0021). Tests that verify the seed values
        # rely on these being present after each TRUNCATE; tests that
        # don't care simply ignore them.
        if any(t == "luck.game_config" for t in existing):
            for game_name, extra_json in (
                ("coinflip", "{}"),
                ("dice", "{}"),
                ("ninetyninex", "{}"),
                ("hotcold", "{}"),
                (
                    "mines",
                    '{"max_mines":24,"min_mines":1,'
                    '"default_mines":3,"grid_size":25}',
                ),
                (
                    "blackjack",
                    '{"commission_bps":450,'
                    '"rules":"vegas_s17_3to2_noins_nosplit","decks":6}',
                ),
                (
                    "roulette",
                    '{"commission_bps":236,'
                    '"variant":"european_single_zero"}',
                ),
                ("diceduel", "{}"),
                ("stakingduel", "{}"),
            ):
                await conn.execute(
                    """
                    INSERT INTO luck.game_config
                      (game_name, enabled, min_bet, max_bet, house_edge_bps,
                       extra_config, updated_by)
                    VALUES ($1, TRUE, 100, 500000, 500, $2::jsonb, 0)
                    ON CONFLICT (game_name) DO NOTHING
                    """,
                    game_name,
                    extra_json,
                )

        if any(t == "luck.global_config" for t in existing):
            await conn.execute(
                """
                INSERT INTO luck.global_config (key, value_int, updated_by)
                VALUES
                    ('raffle_rake_bps',            100, 0),
                    ('raffle_ticket_threshold_g',  100, 0),
                    ('bet_rate_limit_per_60s',     30,  0),
                    ('command_rate_limit_per_60s', 30,  0)
                ON CONFLICT (key) DO NOTHING
                """
            )
    finally:
        await conn.close()
