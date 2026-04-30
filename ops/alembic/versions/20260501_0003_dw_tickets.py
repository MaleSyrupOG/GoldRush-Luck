"""dw.deposit_tickets and dw.withdraw_tickets with terminal-state immutability.

Revision ID: 0003_dw_tickets
Revises: 0002_core_audit_log
Create Date: 2026-05-01

Implements D/W design §3.2.

Both ticket tables share lifecycle states:
    open → claimed → confirmed
                 ↘ cancelled / expired
Once a row reaches `confirmed`, `cancelled`, or `expired`, its `status`
column is permanently locked by a BEFORE UPDATE trigger that raises if the
new value differs from the old. Other columns can still be updated
(e.g. ``last_activity_at``) but never the terminal status.

The withdraw table also captures `fee` (in G) at creation time and
`amount_delivered` (= amount - fee) at confirmation, persisted on the
row so the receipt that the user sees is auditable forever.
"""

from alembic import op


revision = "0003_dw_tickets"
down_revision = "0002_core_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- deposit_tickets ----------
    # NB: discord_id has no FK to core.users — the user may not exist yet
    # on a first deposit. confirm_deposit() creates the user idempotently.
    op.execute("""
        CREATE TABLE dw.deposit_tickets (
            id                  BIGSERIAL   PRIMARY KEY,
            ticket_uid          TEXT        NOT NULL UNIQUE,
            discord_id          BIGINT      NOT NULL,
            char_name           TEXT        NOT NULL,
            realm               TEXT        NOT NULL,
            region              TEXT        NOT NULL CHECK (region IN ('EU','NA')),
            faction             TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
            amount              BIGINT      NOT NULL CHECK (amount > 0),
            status              TEXT        NOT NULL
                                            CHECK (status IN ('open','claimed','confirmed','cancelled','expired','disputed')),
            claimed_by          BIGINT,
            claimed_at          TIMESTAMPTZ,
            confirmed_at        TIMESTAMPTZ,
            cancelled_at        TIMESTAMPTZ,
            cancel_reason       TEXT,
            thread_id           BIGINT      NOT NULL UNIQUE,
            parent_channel_id   BIGINT      NOT NULL,
            expires_at          TIMESTAMPTZ NOT NULL,
            last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_deposit_status_created  ON dw.deposit_tickets (status, created_at DESC);
        CREATE INDEX idx_deposit_user_created    ON dw.deposit_tickets (discord_id, created_at DESC);
        CREATE INDEX idx_deposit_cashier_claimed ON dw.deposit_tickets (claimed_by, claimed_at DESC);
        CREATE INDEX idx_deposit_open_expires    ON dw.deposit_tickets (expires_at)
            WHERE status IN ('open','claimed');
    """)

    # ---------- withdraw_tickets ----------
    op.execute("""
        CREATE TABLE dw.withdraw_tickets (
            id                  BIGSERIAL   PRIMARY KEY,
            ticket_uid          TEXT        NOT NULL UNIQUE,
            discord_id          BIGINT      NOT NULL
                                            REFERENCES core.users(discord_id)
                                            ON DELETE RESTRICT,
            char_name           TEXT        NOT NULL,
            realm               TEXT        NOT NULL,
            region              TEXT        NOT NULL CHECK (region IN ('EU','NA')),
            faction             TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
            amount              BIGINT      NOT NULL CHECK (amount > 0),
            fee                 BIGINT      NOT NULL CHECK (fee >= 0),
            amount_delivered    BIGINT,
            status              TEXT        NOT NULL
                                            CHECK (status IN ('open','claimed','confirmed','cancelled','expired','disputed')),
            claimed_by          BIGINT,
            claimed_at          TIMESTAMPTZ,
            confirmed_at        TIMESTAMPTZ,
            cancelled_at        TIMESTAMPTZ,
            cancel_reason       TEXT,
            thread_id           BIGINT      NOT NULL UNIQUE,
            parent_channel_id   BIGINT      NOT NULL,
            expires_at          TIMESTAMPTZ NOT NULL,
            last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_withdraw_status_created  ON dw.withdraw_tickets (status, created_at DESC);
        CREATE INDEX idx_withdraw_user_created    ON dw.withdraw_tickets (discord_id, created_at DESC);
        CREATE INDEX idx_withdraw_cashier_claimed ON dw.withdraw_tickets (claimed_by, claimed_at DESC);
        CREATE INDEX idx_withdraw_open_expires    ON dw.withdraw_tickets (expires_at)
            WHERE status IN ('open','claimed');
    """)

    # ---------- terminal-state immutability triggers ----------
    # Once status is confirmed/cancelled/expired, it is locked. We use a
    # shared function for both tables.
    op.execute("""
        CREATE OR REPLACE FUNCTION dw.ticket_terminal_status_immutable()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('confirmed','cancelled','expired')
               AND OLD.status IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION
                    'cannot transition % from terminal status % to %',
                    TG_TABLE_NAME, OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER deposit_terminal_immutable
            BEFORE UPDATE ON dw.deposit_tickets
            FOR EACH ROW EXECUTE FUNCTION dw.ticket_terminal_status_immutable();

        CREATE TRIGGER withdraw_terminal_immutable
            BEFORE UPDATE ON dw.withdraw_tickets
            FOR EACH ROW EXECUTE FUNCTION dw.ticket_terminal_status_immutable();
    """)

    # Per-table privileges: goldrush_dw has full RW; readonly has SELECT.
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON dw.deposit_tickets, dw.withdraw_tickets TO goldrush_dw;
        GRANT USAGE, SELECT ON SEQUENCE dw.deposit_tickets_id_seq, dw.withdraw_tickets_id_seq TO goldrush_dw;
        GRANT SELECT ON dw.deposit_tickets, dw.withdraw_tickets TO goldrush_readonly;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS withdraw_terminal_immutable ON dw.withdraw_tickets;
        DROP TRIGGER IF EXISTS deposit_terminal_immutable  ON dw.deposit_tickets;
        DROP FUNCTION IF EXISTS dw.ticket_terminal_status_immutable();
        DROP TABLE IF EXISTS dw.withdraw_tickets CASCADE;
        DROP TABLE IF EXISTS dw.deposit_tickets  CASCADE;
    """)
