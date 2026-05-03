"""fairness.user_seeds + fairness.history with append-only triggers.

Revision ID: 0019_fairness_user_seeds
Revises: 0018_core_list_audit_events
Create Date: 2026-05-04

Implements Luck design §3.3, §4.2 — the per-user provably-fair seed
storage:

- ``fairness.user_seeds`` (one row per user; carries the current
  ``server_seed`` (sensitive), ``server_seed_hash`` (public commitment),
  ``client_seed`` (user-editable), and ``nonce`` (monotonic counter
  reset at rotation)).
- ``fairness.history`` (append-only; one row per past rotation;
  reveals the ``server_seed`` so users can verify any past bet
  retrospectively).
- Append-only triggers on ``fairness.history`` matching the
  immutability semantics of ``core.audit_log`` (any UPDATE / DELETE
  raises ``raise_exception``, even from the admin role).
- Per-table grants:
    - deathroll_luck       : SELECT, INSERT, UPDATE on both tables
    - deathroll_dw         : SELECT, INSERT, UPDATE on both tables
                             (D/W may rotate during a withdraw flow
                             in v1.x; pre-positioned grants per
                             spec §3.1)
    - deathroll_readonly   : SELECT only

The default privileges already declared in
``ops/postgres/01-schemas-grants.sql`` ALTER DEFAULT PRIVILEGES
gives the bot roles the SELECT/INSERT/UPDATE on future tables in
the ``fairness`` schema; this migration leans on that for the
day-zero grants and only spells out the readonly grant explicitly
(which the default doesn't cover for fairness — readonly's
default is configured via the alembic env on the dw side already).
"""

from alembic import op

# revision identifiers
revision = "0019_fairness_user_seeds"
down_revision = "0018_core_list_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # fairness.user_seeds
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE fairness.user_seeds (
            discord_id        BIGINT      PRIMARY KEY
                                          REFERENCES core.users(discord_id)
                                          ON DELETE RESTRICT,
            server_seed       BYTEA       NOT NULL,
            server_seed_hash  BYTEA       NOT NULL,
            client_seed       TEXT        NOT NULL,
            nonce             BIGINT      NOT NULL DEFAULT 0,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rotated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # -----------------------------------------------------------------
    # fairness.history
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TABLE fairness.history (
            id                    BIGSERIAL   PRIMARY KEY,
            discord_id            BIGINT      NOT NULL
                                              REFERENCES core.users(discord_id),
            revealed_server_seed  BYTEA       NOT NULL,
            server_seed_hash      BYTEA       NOT NULL,
            client_seed           TEXT        NOT NULL,
            last_nonce            BIGINT      NOT NULL,
            started_at            TIMESTAMPTZ NOT NULL,
            rotated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rotated_by            TEXT        NOT NULL
                                              CHECK (rotated_by IN ('user','system','admin'))
        );
    """)

    op.execute("""
        CREATE INDEX idx_fairness_history_user
        ON fairness.history (discord_id, rotated_at DESC);
    """)

    # -----------------------------------------------------------------
    # Append-only triggers on fairness.history
    #
    # Same shape as ``core.audit_log_block_mutation`` from migration
    # 0002; we re-create the trigger fn under the fairness schema so
    # the dependency graph is local. UPDATE and DELETE both raise.
    # TRUNCATE bypasses the trigger (intentional — used in test
    # cleanup; ``GRANT TRUNCATE`` is NOT given to bot roles).
    # -----------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION fairness.history_block_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'fairness.history is append-only; %s rejected',
                TG_OP;
        END;
        $$;
    """)

    op.execute("""
        CREATE TRIGGER fairness_history_no_update
        BEFORE UPDATE ON fairness.history
        FOR EACH ROW
        EXECUTE FUNCTION fairness.history_block_mutation();
    """)

    op.execute("""
        CREATE TRIGGER fairness_history_no_delete
        BEFORE DELETE ON fairness.history
        FOR EACH ROW
        EXECUTE FUNCTION fairness.history_block_mutation();
    """)

    # -----------------------------------------------------------------
    # Per-table grants.
    #
    # ``ALTER DEFAULT PRIVILEGES IN SCHEMA fairness`` from
    # ``01-schemas-grants.sql`` already grants
    # ``SELECT, INSERT, UPDATE`` to deathroll_luck and deathroll_dw on
    # tables created in this schema — but the default applies only
    # to FUTURE tables created by the role that ran the ALTER. Since
    # alembic runs as deathroll_admin and the default is configured
    # against the same role, the grants land automatically. We make
    # the grants explicit here so the migration is self-evident in
    # code review and so the readonly grant (not in the default
    # privileges block) is unmistakable.
    # -----------------------------------------------------------------
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON fairness.user_seeds TO deathroll_luck;
        GRANT SELECT, INSERT, UPDATE ON fairness.user_seeds TO deathroll_dw;
        GRANT SELECT                  ON fairness.user_seeds TO deathroll_readonly;

        GRANT SELECT, INSERT, UPDATE ON fairness.history TO deathroll_luck;
        GRANT SELECT, INSERT, UPDATE ON fairness.history TO deathroll_dw;
        GRANT SELECT                  ON fairness.history TO deathroll_readonly;
    """)

    # The BIGSERIAL on fairness.history.id needs a sequence USAGE
    # grant for INSERTs from the bot roles to populate the id.
    op.execute("""
        GRANT USAGE, SELECT ON SEQUENCE fairness.history_id_seq
            TO deathroll_luck, deathroll_dw;
    """)

    # deathroll_poker is created by init.sh only when its password is
    # set (it is 'disabled' in v1). Wrap the grant so the migration
    # succeeds even when the role does not exist yet.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deathroll_poker') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON fairness.user_seeds TO deathroll_poker';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON fairness.history    TO deathroll_poker';
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE fairness.history_id_seq TO deathroll_poker';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Drop in reverse order; index dropped with table; triggers dropped
    # with the function via CASCADE.
    op.execute("DROP TABLE IF EXISTS fairness.history CASCADE;")
    op.execute("DROP TABLE IF EXISTS fairness.user_seeds CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS fairness.history_block_mutation() CASCADE;")
