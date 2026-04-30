"""core.users, core.balances, treasury seed, per-table grants.

Revision ID: 0001_core_users_balances
Revises:
Create Date: 2026-05-01

Implements Luck design §3.3 and D/W design §3.1, §3.2:
- Creates core.users with the banned-flag columns.
- Creates core.balances with the four CHECK constraints (balance >= 0,
  locked_balance >= 0, total_wagered >= 0, total_won >= 0) and the
  optimistic-locking version column.
- Adds the per-table grants:
    - goldrush_dw       : SELECT, INSERT, UPDATE on both tables
    - goldrush_luck     : SELECT only
    - goldrush_poker    : SELECT only
    - goldrush_readonly : SELECT
- Seeds the operator-controlled treasury account at discord_id=0
  (idempotent ON CONFLICT DO NOTHING).
"""

from alembic import op


# revision identifiers
revision = "0001_core_users_balances"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE core.users (
            discord_id      BIGINT      PRIMARY KEY,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            banned          BOOLEAN     NOT NULL DEFAULT FALSE,
            banned_reason   TEXT,
            banned_at       TIMESTAMPTZ
        );
    """)

    op.execute("""
        CREATE TABLE core.balances (
            discord_id      BIGINT      PRIMARY KEY
                                        REFERENCES core.users(discord_id)
                                        ON DELETE RESTRICT,
            balance         BIGINT      NOT NULL DEFAULT 0
                                        CHECK (balance >= 0),
            locked_balance  BIGINT      NOT NULL DEFAULT 0
                                        CHECK (locked_balance >= 0),
            total_wagered   BIGINT      NOT NULL DEFAULT 0
                                        CHECK (total_wagered >= 0),
            total_won       BIGINT      NOT NULL DEFAULT 0
                                        CHECK (total_won >= 0),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            version         BIGINT      NOT NULL DEFAULT 0
        );
    """)

    # Per-table grants. init.sql granted USAGE on the schema; here we attach
    # the actual table-level privileges per the spec's role matrix.
    # IMPORTANT: goldrush_luck and goldrush_poker get SELECT only — they
    # cannot mint or destroy balance. Only goldrush_dw can write here, and
    # even then only via SECURITY DEFINER functions (which bypass these
    # grants because the function runs as its owner, goldrush_admin).
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON core.users    TO goldrush_dw;
        GRANT SELECT, INSERT, UPDATE ON core.balances TO goldrush_dw;
        GRANT SELECT                  ON core.users    TO goldrush_luck;
        GRANT SELECT                  ON core.balances TO goldrush_luck;
        GRANT SELECT                  ON core.users    TO goldrush_readonly;
        GRANT SELECT                  ON core.balances TO goldrush_readonly;
    """)

    # goldrush_poker is created by init.sh only when its password is set
    # (it is 'disabled' in v1). Wrap the grant so the migration succeeds
    # even when the role does not exist yet.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'goldrush_poker') THEN
                EXECUTE 'GRANT SELECT ON core.users    TO goldrush_poker';
                EXECUTE 'GRANT SELECT ON core.balances TO goldrush_poker';
            END IF;
        END
        $$;
    """)

    # Treasury seed (discord_id=0). The actual gold lives in the in-game
    # guild bank; this row is the bot-side ledger of how much SHOULD be
    # there as accumulated fee revenue.
    op.execute("""
        INSERT INTO core.users (discord_id, created_at)
        VALUES (0, NOW())
        ON CONFLICT (discord_id) DO NOTHING;

        INSERT INTO core.balances (discord_id, balance)
        VALUES (0, 0)
        ON CONFLICT (discord_id) DO NOTHING;
    """)


def downgrade() -> None:
    # Drop in reverse order; balance has FK to users.
    op.execute("DROP TABLE IF EXISTS core.balances CASCADE;")
    op.execute("DROP TABLE IF EXISTS core.users    CASCADE;")
