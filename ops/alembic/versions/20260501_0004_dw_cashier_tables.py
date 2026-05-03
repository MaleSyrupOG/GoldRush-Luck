"""dw.cashier_characters, cashier_status, cashier_sessions, cashier_stats.

Revision ID: 0004_dw_cashier_tables
Revises: 0003_dw_tickets
Create Date: 2026-05-01

Implements D/W design §3.2:
- cashier_characters: per-cashier list of in-game characters with region
  and faction, soft-deletable. Used by claim_ticket to verify
  region-compatibility.
- cashier_status: current online/offline/break state per cashier.
- cashier_sessions: per-period audit of online/break time, used to
  reconstruct cashier_stats and to spot abuse.
- cashier_stats: denormalised aggregates (volume, ratios, average
  claim→confirm time, last activity). Recomputable from raw data; cached
  here to keep `/admin cashier-stats` queries O(1).
"""

from alembic import op


revision = "0004_dw_cashier_tables"
down_revision = "0003_dw_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dw.cashier_characters (
            id              BIGSERIAL   PRIMARY KEY,
            discord_id      BIGINT      NOT NULL,
            char_name       TEXT        NOT NULL,
            realm           TEXT        NOT NULL,
            region          TEXT        NOT NULL CHECK (region IN ('EU','NA')),
            faction         TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            removed_at      TIMESTAMPTZ,
            UNIQUE (discord_id, char_name, realm, region)
        );

        CREATE INDEX idx_cashier_chars_active
            ON dw.cashier_characters (discord_id) WHERE is_active = TRUE;
        CREATE INDEX idx_cashier_chars_by_region
            ON dw.cashier_characters (region) WHERE is_active = TRUE;
    """)

    op.execute("""
        CREATE TABLE dw.cashier_status (
            discord_id      BIGINT      PRIMARY KEY,
            status          TEXT        NOT NULL
                                        CHECK (status IN ('online','offline','break')),
            set_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            auto_offline_at TIMESTAMPTZ,
            last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_cashier_status_online
            ON dw.cashier_status (set_at DESC) WHERE status = 'online';
    """)

    op.execute("""
        CREATE TABLE dw.cashier_sessions (
            id              BIGSERIAL   PRIMARY KEY,
            discord_id      BIGINT      NOT NULL,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at        TIMESTAMPTZ,
            duration_s      BIGINT,
            end_reason      TEXT
                CHECK (end_reason IN ('manual_offline','manual_break','auto_disconnect','admin_force','expired'))
        );

        CREATE INDEX idx_cashier_sessions_user_started
            ON dw.cashier_sessions (discord_id, started_at DESC);
    """)

    op.execute("""
        CREATE TABLE dw.cashier_stats (
            discord_id              BIGINT      PRIMARY KEY,
            deposits_completed      BIGINT      NOT NULL DEFAULT 0,
            deposits_cancelled      BIGINT      NOT NULL DEFAULT 0,
            withdraws_completed     BIGINT      NOT NULL DEFAULT 0,
            withdraws_cancelled     BIGINT      NOT NULL DEFAULT 0,
            total_volume_g          BIGINT      NOT NULL DEFAULT 0,
            total_online_seconds    BIGINT      NOT NULL DEFAULT 0,
            avg_claim_to_confirm_s  INTEGER,
            last_active_at          TIMESTAMPTZ,
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        GRANT SELECT, INSERT, UPDATE
            ON dw.cashier_characters, dw.cashier_status,
               dw.cashier_sessions,    dw.cashier_stats
            TO deathroll_dw;
        GRANT USAGE, SELECT
            ON SEQUENCE dw.cashier_characters_id_seq,
                        dw.cashier_sessions_id_seq
            TO deathroll_dw;
        GRANT SELECT
            ON dw.cashier_characters, dw.cashier_status,
               dw.cashier_sessions,    dw.cashier_stats
            TO deathroll_readonly;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS dw.cashier_stats      CASCADE;
        DROP TABLE IF EXISTS dw.cashier_sessions   CASCADE;
        DROP TABLE IF EXISTS dw.cashier_status     CASCADE;
        DROP TABLE IF EXISTS dw.cashier_characters CASCADE;
    """)
