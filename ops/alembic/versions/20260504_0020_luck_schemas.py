"""luck.* schemas — game_config, bets, bet_rounds, game_sessions,
rate_limit_entries, raffle_*, leaderboard_snapshot, global_config.

Revision ID: 0020_luck_schemas
Revises: 0019_fairness_user_seeds
Create Date: 2026-05-04

Implements Luck design §3.3 — every persistence table the Luck
bot needs, plus the supporting indexes, check/unique constraints,
the append-only trigger on luck.raffle_draws, and the per-table
grant matrix.

Tables created (in FK-safe order):

1. luck.game_config         — admin-tunable per-game runtime config
2. luck.channel_binding     — game ↔ Discord channel binding
3. luck.bets                — primary bet record (one row per /bet)
4. luck.bet_rounds          — multi-round games (BJ, Mines, Dice Duel)
5. luck.game_sessions       — orphan-detection state for in-flight games
6. luck.rate_limit_entries  — sliding-window counters for spam protection
7. luck.raffle_periods      — monthly raffle period rows
8. luck.raffle_tickets      — per-bet ticket grants for the raffle
9. luck.raffle_draws        — winner records (APPEND-ONLY)
10. luck.leaderboard_snapshot — periodic leaderboard caches
11. luck.global_config      — key/value table (rake bps, rate limits, etc.)

Constraints + indexes per spec §3.3. The
``(discord_id, idempotency_key)`` UNIQUE on ``luck.bets`` is the
**double-charge prevention** — every bet MUST carry an
idempotency key, and a duplicate (`discord:<interaction_id>`)
returns the existing bet rather than creating a second one.

The append-only trigger on ``luck.raffle_draws`` mirrors the
``core.audit_log`` and ``fairness.history`` immutability:
UPDATE and DELETE both raise; TRUNCATE bypasses (intentional
for test cleanup).
"""

from alembic import op

# revision identifiers
revision = "0020_luck_schemas"
down_revision = "0019_fairness_user_seeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. luck.game_config
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.game_config (
            game_name           TEXT        PRIMARY KEY,
            enabled             BOOLEAN     NOT NULL DEFAULT TRUE,
            min_bet             BIGINT      NOT NULL CHECK (min_bet > 0),
            max_bet             BIGINT      NOT NULL CHECK (max_bet >= min_bet),
            house_edge_bps      INT         NOT NULL
                                            CHECK (house_edge_bps BETWEEN 0 AND 10000),
            payout_multiplier   NUMERIC(8,4),
            extra_config        JSONB       NOT NULL DEFAULT '{}'::jsonb,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by          BIGINT      NOT NULL
        );
    """)

    # -----------------------------------------------------------------
    # 2. luck.channel_binding
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.channel_binding (
            game_name       TEXT        PRIMARY KEY
                                        REFERENCES luck.game_config(game_name)
                                        ON UPDATE CASCADE,
            channel_id      BIGINT      NOT NULL UNIQUE,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by      BIGINT      NOT NULL
        );
    """)

    # -----------------------------------------------------------------
    # 3. luck.bets
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.bets (
            id                BIGSERIAL   PRIMARY KEY,
            bet_uid           TEXT        NOT NULL UNIQUE,
            discord_id        BIGINT      NOT NULL
                                          REFERENCES core.users(discord_id),
            game_name         TEXT        NOT NULL
                                          REFERENCES luck.game_config(game_name),
            channel_id        BIGINT      NOT NULL,
            bet_amount        BIGINT      NOT NULL CHECK (bet_amount > 0),
            selection         JSONB       NOT NULL,
            status            TEXT        NOT NULL
                                          CHECK (status IN ('open','resolved_win',
                                                            'resolved_loss','resolved_tie',
                                                            'refunded','voided')),
            payout_amount     BIGINT      NOT NULL DEFAULT 0
                                          CHECK (payout_amount >= 0),
            profit            BIGINT,
            server_seed_hash  BYTEA       NOT NULL,
            client_seed       TEXT        NOT NULL,
            nonce             BIGINT      NOT NULL,
            outcome           JSONB,
            idempotency_key   TEXT        NOT NULL,
            placed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at       TIMESTAMPTZ,
            UNIQUE (discord_id, idempotency_key)
        );
    """)

    op.execute("""
        CREATE INDEX idx_bets_user_ts
            ON luck.bets (discord_id, placed_at DESC);
        CREATE INDEX idx_bets_game_ts
            ON luck.bets (game_name, placed_at DESC);
        CREATE INDEX idx_bets_status
            ON luck.bets (status) WHERE status = 'open';
        CREATE INDEX idx_bets_resolved
            ON luck.bets (resolved_at DESC) WHERE status LIKE 'resolved%';
    """)

    # -----------------------------------------------------------------
    # 4. luck.bet_rounds
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.bet_rounds (
            id              BIGSERIAL   PRIMARY KEY,
            bet_id          BIGINT      NOT NULL
                                        REFERENCES luck.bets(id)
                                        ON DELETE RESTRICT,
            round_index     INT         NOT NULL,
            nonce           BIGINT      NOT NULL,
            action          TEXT        NOT NULL,
            action_data     JSONB       NOT NULL DEFAULT '{}'::jsonb,
            outcome         JSONB       NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (bet_id, round_index)
        );
    """)

    # -----------------------------------------------------------------
    # 5. luck.game_sessions
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.game_sessions (
            bet_id          BIGINT      PRIMARY KEY
                                        REFERENCES luck.bets(id)
                                        ON DELETE RESTRICT,
            state           JSONB       NOT NULL,
            expires_at      TIMESTAMPTZ NOT NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_session_expires
            ON luck.game_sessions (expires_at);
    """)

    # -----------------------------------------------------------------
    # 6. luck.rate_limit_entries
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.rate_limit_entries (
            id              BIGSERIAL   PRIMARY KEY,
            discord_id      BIGINT      NOT NULL,
            scope           TEXT        NOT NULL,
            bucket_start    TIMESTAMPTZ NOT NULL,
            count           INT         NOT NULL DEFAULT 1,
            UNIQUE (discord_id, scope, bucket_start)
        );
        CREATE INDEX idx_ratelimit_lookup
            ON luck.rate_limit_entries (discord_id, scope, bucket_start DESC);
    """)

    # -----------------------------------------------------------------
    # 7. luck.raffle_periods
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.raffle_periods (
            id              BIGSERIAL   PRIMARY KEY,
            period_label    TEXT        NOT NULL UNIQUE,
            starts_at       TIMESTAMPTZ NOT NULL,
            ends_at         TIMESTAMPTZ NOT NULL CHECK (ends_at > starts_at),
            pool_amount     BIGINT      NOT NULL DEFAULT 0
                                        CHECK (pool_amount >= 0),
            status          TEXT        NOT NULL
                                        CHECK (status IN ('active','drawing','closed')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # -----------------------------------------------------------------
    # 8. luck.raffle_tickets
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.raffle_tickets (
            id              BIGSERIAL   PRIMARY KEY,
            period_id       BIGINT      NOT NULL
                                        REFERENCES luck.raffle_periods(id),
            discord_id      BIGINT      NOT NULL
                                        REFERENCES core.users(discord_id),
            bet_id          BIGINT      REFERENCES luck.bets(id),
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_tickets_period_user
            ON luck.raffle_tickets (period_id, discord_id);
        CREATE INDEX idx_tickets_period_ts
            ON luck.raffle_tickets (period_id, granted_at DESC);
    """)

    # -----------------------------------------------------------------
    # 9. luck.raffle_draws  (APPEND-ONLY)
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.raffle_draws (
            id                    BIGSERIAL   PRIMARY KEY,
            period_id             BIGINT      NOT NULL UNIQUE
                                              REFERENCES luck.raffle_periods(id),
            drawn_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            pool_amount           BIGINT      NOT NULL,
            revealed_server_seed  BYTEA       NOT NULL,
            server_seed_hash      BYTEA       NOT NULL,
            client_seed_used      TEXT        NOT NULL,
            nonces_used           JSONB       NOT NULL,
            winners               JSONB       NOT NULL,
            total_tickets         BIGINT      NOT NULL
        );
    """)

    # Append-only trigger on luck.raffle_draws.
    op.execute("""
        CREATE OR REPLACE FUNCTION luck.raffle_draws_block_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'luck.raffle_draws is append-only; %s rejected',
                TG_OP;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER raffle_draws_no_update
        BEFORE UPDATE ON luck.raffle_draws
        FOR EACH ROW
        EXECUTE FUNCTION luck.raffle_draws_block_mutation();
    """)

    op.execute("""
        CREATE TRIGGER raffle_draws_no_delete
        BEFORE DELETE ON luck.raffle_draws
        FOR EACH ROW
        EXECUTE FUNCTION luck.raffle_draws_block_mutation();
    """)

    # -----------------------------------------------------------------
    # 10. luck.leaderboard_snapshot
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE luck.leaderboard_snapshot (
            period          TEXT        NOT NULL
                                        CHECK (period IN ('daily','weekly',
                                                          'monthly','all_time')),
            category        TEXT        NOT NULL
                                        CHECK (category IN ('top_wagered',
                                                            'top_won',
                                                            'top_big_wins')),
            snapshot        JSONB       NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (period, category)
        );
    """)

    # -----------------------------------------------------------------
    # 11. luck.global_config (key/value)
    # -----------------------------------------------------------------
    # Mirrors dw.global_config shape: textual key, integer value,
    # actor + timestamp. Story 2.9 will seed this with raffle_rake_bps,
    # raffle_ticket_threshold_g, bet_rate_limit_per_60s, and
    # command_rate_limit_per_60s.
    op.execute("""
        CREATE TABLE luck.global_config (
            key             TEXT        PRIMARY KEY,
            value_int       BIGINT,
            value_text      TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by      BIGINT      NOT NULL
        );
    """)

    # -----------------------------------------------------------------
    # Per-table grants
    #
    # The ALTER DEFAULT PRIVILEGES in 01-schemas-grants.sql configures
    # SELECT, INSERT, UPDATE on all FUTURE luck.* tables for
    # deathroll_luck. We make the grants explicit per spec §3.1 to
    # surface the privilege boundaries unambiguously in code review;
    # readonly grants (NOT covered by the default for luck) are also
    # made explicit.
    # -----------------------------------------------------------------
    for tbl in (
        "luck.game_config",
        "luck.channel_binding",
        "luck.bets",
        "luck.bet_rounds",
        "luck.game_sessions",
        "luck.rate_limit_entries",
        "luck.raffle_periods",
        "luck.raffle_tickets",
        "luck.raffle_draws",
        "luck.leaderboard_snapshot",
        "luck.global_config",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {tbl} TO deathroll_luck;")
        op.execute(f"GRANT SELECT                  ON {tbl} TO deathroll_readonly;")

    # BIGSERIAL sequence USAGE for the bot role on every sequence we
    # just created so INSERTs can populate the id columns.
    for seq in (
        "luck.bets_id_seq",
        "luck.bet_rounds_id_seq",
        "luck.rate_limit_entries_id_seq",
        "luck.raffle_periods_id_seq",
        "luck.raffle_tickets_id_seq",
        "luck.raffle_draws_id_seq",
    ):
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {seq} TO deathroll_luck;")

    # deathroll_poker is created by init.sh only when its password is
    # set (it is 'disabled' in v1). Wrap conditionally.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deathroll_poker') THEN
                -- Poker reads luck.* for cross-bot leaderboard or raffle
                -- visibility; no write privilege.
                EXECUTE 'GRANT SELECT ON luck.game_config         TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.channel_binding     TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.bets                TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.bet_rounds          TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.game_sessions       TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.rate_limit_entries  TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.raffle_periods      TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.raffle_tickets      TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.raffle_draws        TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.leaderboard_snapshot TO deathroll_poker';
                EXECUTE 'GRANT SELECT ON luck.global_config       TO deathroll_poker';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Drop in reverse FK order. CASCADE catches the indexes + triggers.
    op.execute("DROP TABLE IF EXISTS luck.global_config CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.leaderboard_snapshot CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.raffle_draws CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS luck.raffle_draws_block_mutation() CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.raffle_tickets CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.raffle_periods CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.rate_limit_entries CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.game_sessions CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.bet_rounds CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.bets CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.channel_binding CASCADE;")
    op.execute("DROP TABLE IF EXISTS luck.game_config CASCADE;")
