"""core.audit_log + immutability triggers + hash chain helper.

Revision ID: 0002_core_audit_log
Revises: 0001_core_users_balances
Create Date: 2026-05-01

Implements Luck design §3.3 and D/W design §3.2:
- Creates core.audit_log with all spec columns and indexes.
- Installs append-only triggers blocking UPDATE and DELETE.
- Enables the pgcrypto extension (HMAC-SHA256 used by the chain).
- Creates core.audit_chain_state holding the current chain key and the
  last row hash, plus a SECURITY DEFINER function
  core.audit_log_insert_with_chain() that every later SECURITY DEFINER
  function (apply_bet, confirm_deposit, etc.) calls to append a row
  with prev_hash and row_hash linked to the previous row.

Chain-key provisioning:
- The chain key is read from the Postgres setting ``app.audit_chain_key``
  which is set once per database via:

      ALTER DATABASE deathroll SET app.audit_chain_key = '<hex>';

  documented in docs/operations.md. The migration cannot read environment
  variables, so it leaves the key absent and relies on the operator to set
  it post-migration. The function raises on missing key, which makes the
  configuration step impossible to forget.

Append-only enforcement:
- The two triggers raise unconditionally on UPDATE and DELETE; even
  deathroll_admin cannot bypass them without explicitly disabling the
  triggers — which would itself appear in the system catalog.
"""

from alembic import op


revision = "0002_core_audit_log"
down_revision = "0001_core_users_balances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto provides hmac() — used by the chain function.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.execute("""
        CREATE TABLE core.audit_log (
            id              BIGSERIAL   PRIMARY KEY,
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            actor_type      TEXT        NOT NULL
                                        CHECK (actor_type IN ('user','admin','system','cashier','bot')),
            actor_id        BIGINT      NOT NULL,
            target_id       BIGINT      NOT NULL
                                        REFERENCES core.users(discord_id)
                                        ON DELETE RESTRICT,
            action          TEXT        NOT NULL,
            amount          BIGINT,
            balance_before  BIGINT      NOT NULL,
            balance_after   BIGINT      NOT NULL,
            reason          TEXT,
            ref_type        TEXT        NOT NULL,
            ref_id          TEXT,
            bot_name        TEXT        NOT NULL,
            metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
            prev_hash       BYTEA,
            row_hash        BYTEA       NOT NULL
        );

        CREATE INDEX idx_audit_target_ts ON core.audit_log (target_id, ts DESC);
        CREATE INDEX idx_audit_action_ts ON core.audit_log (action,    ts DESC);
        CREATE INDEX idx_audit_ref       ON core.audit_log (ref_type,  ref_id);
    """)

    # Append-only triggers. Use one shared function that simply raises.
    op.execute("""
        CREATE OR REPLACE FUNCTION core.audit_log_immutable()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only — UPDATE/DELETE forbidden';
        END;
        $$;

        CREATE TRIGGER audit_log_no_update
            BEFORE UPDATE ON core.audit_log
            FOR EACH ROW EXECUTE FUNCTION core.audit_log_immutable();

        CREATE TRIGGER audit_log_no_delete
            BEFORE DELETE ON core.audit_log
            FOR EACH ROW EXECUTE FUNCTION core.audit_log_immutable();
    """)

    # Chain state: stores the hash of the most recent row so the next
    # insertion can chain off it.
    op.execute("""
        CREATE TABLE core.audit_chain_state (
            id             INT     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            last_row_hash  BYTEA
        );

        INSERT INTO core.audit_chain_state (id, last_row_hash)
        VALUES (1, NULL)
        ON CONFLICT (id) DO NOTHING;
    """)

    # The chain helper. Owned by the migration role (deathroll_admin) and
    # marked SECURITY DEFINER so it can be granted EXECUTE to bot roles
    # while still running with admin privileges (which gives it the rights
    # needed to INSERT into audit_log and UPDATE the chain state).
    #
    # Signature design:
    # - The caller passes every audit-log column except id, ts, prev_hash
    #   and row_hash. The function fills in those four.
    # - The caller's transaction sees the new row only after function
    #   returns; readers in other transactions see it after commit.
    op.execute("""
        CREATE OR REPLACE FUNCTION core.audit_log_insert_with_chain(
            p_actor_type     TEXT,
            p_actor_id       BIGINT,
            p_target_id      BIGINT,
            p_action         TEXT,
            p_amount         BIGINT,
            p_balance_before BIGINT,
            p_balance_after  BIGINT,
            p_reason         TEXT,
            p_ref_type       TEXT,
            p_ref_id         TEXT,
            p_bot_name       TEXT,
            p_metadata       JSONB
        ) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = core, pg_catalog AS $$
        DECLARE
            v_chain_key  BYTEA;
            v_prev_hash  BYTEA;
            v_row_hash   BYTEA;
            v_canonical  TEXT;
            v_new_id     BIGINT;
            v_now        TIMESTAMPTZ := NOW();
        BEGIN
            -- Resolve the chain key. The operator sets it once per database
            -- with:
            --     ALTER DATABASE deathroll
            --         SET app.audit_chain_key = '<hex-encoded-32-bytes>';
            -- If unset, abort loudly so the misconfiguration is impossible
            -- to miss.
            BEGIN
                v_chain_key := decode(current_setting('app.audit_chain_key'), 'hex');
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'audit chain key not configured (set app.audit_chain_key on the database)';
            END;
            IF v_chain_key IS NULL OR octet_length(v_chain_key) = 0 THEN
                RAISE EXCEPTION 'audit chain key is empty';
            END IF;

            -- Lock the chain state row so two concurrent inserts cannot
            -- both read the same prev_hash.
            SELECT last_row_hash INTO v_prev_hash
            FROM core.audit_chain_state
            WHERE id = 1
            FOR UPDATE;

            -- Compose the canonical content of THIS row (without the hashes).
            v_canonical := jsonb_build_object(
                'ts',             v_now,
                'actor_type',     p_actor_type,
                'actor_id',       p_actor_id,
                'target_id',      p_target_id,
                'action',         p_action,
                'amount',         p_amount,
                'balance_before', p_balance_before,
                'balance_after',  p_balance_after,
                'reason',         p_reason,
                'ref_type',       p_ref_type,
                'ref_id',         p_ref_id,
                'bot_name',       p_bot_name,
                'metadata',       p_metadata
            )::text;

            -- row_hash = HMAC-SHA256(chain_key, prev_hash || canonical)
            -- where prev_hash is treated as a 32-byte zero block when NULL
            -- (i.e. for the very first row).
            -- Fully-qualified name because pgcrypto lives in public schema
            -- and our SECURITY DEFINER search_path is locked to core,pg_catalog.
            v_row_hash := public.hmac(
                COALESCE(v_prev_hash, decode('00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000', 'hex')) || convert_to(v_canonical, 'UTF8'),
                v_chain_key,
                'sha256'::text
            );

            INSERT INTO core.audit_log (
                ts, actor_type, actor_id, target_id, action, amount,
                balance_before, balance_after, reason, ref_type, ref_id,
                bot_name, metadata, prev_hash, row_hash
            ) VALUES (
                v_now, p_actor_type, p_actor_id, p_target_id, p_action,
                p_amount, p_balance_before, p_balance_after, p_reason,
                p_ref_type, p_ref_id, p_bot_name, p_metadata,
                v_prev_hash, v_row_hash
            )
            RETURNING id INTO v_new_id;

            UPDATE core.audit_chain_state SET last_row_hash = v_row_hash WHERE id = 1;

            RETURN v_new_id;
        END;
        $$;

        REVOKE ALL ON FUNCTION core.audit_log_insert_with_chain(
            TEXT, BIGINT, BIGINT, TEXT, BIGINT, BIGINT, BIGINT, TEXT,
            TEXT, TEXT, TEXT, JSONB
        ) FROM PUBLIC;

        -- bot roles get EXECUTE; only via this function can they append.
        GRANT EXECUTE ON FUNCTION core.audit_log_insert_with_chain(
            TEXT, BIGINT, BIGINT, TEXT, BIGINT, BIGINT, BIGINT, TEXT,
            TEXT, TEXT, TEXT, JSONB
        ) TO deathroll_dw, deathroll_luck;
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deathroll_poker') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION core.audit_log_insert_with_chain(TEXT, BIGINT, BIGINT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB) TO deathroll_poker';
            END IF;
        END
        $$;
    """)

    # Direct table grants for audit_log. Bots cannot UPDATE or DELETE
    # (the triggers would block it anyway) but they get INSERT so they
    # could call without the chain helper if needed. In practice every
    # write goes through the helper so the chain is consistent.
    # Readonly role gets SELECT for grafana.
    op.execute("""
        GRANT INSERT ON core.audit_log TO deathroll_dw, deathroll_luck;
        GRANT SELECT ON core.audit_log TO deathroll_readonly;

        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deathroll_poker') THEN
                EXECUTE 'GRANT INSERT ON core.audit_log TO deathroll_poker';
            END IF;
        END
        $$;

        -- Sequence permission so the BIGSERIAL works for inserters.
        GRANT USAGE, SELECT ON SEQUENCE core.audit_log_id_seq TO deathroll_dw, deathroll_luck;
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deathroll_poker') THEN
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE core.audit_log_id_seq TO deathroll_poker';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS core.audit_log_insert_with_chain(
            TEXT, BIGINT, BIGINT, TEXT, BIGINT, BIGINT, BIGINT, TEXT,
            TEXT, TEXT, TEXT, JSONB
        );
        DROP TABLE IF EXISTS core.audit_chain_state;
        DROP TRIGGER IF EXISTS audit_log_no_delete ON core.audit_log;
        DROP TRIGGER IF EXISTS audit_log_no_update ON core.audit_log;
        DROP FUNCTION IF EXISTS core.audit_log_immutable();
        DROP TABLE IF EXISTS core.audit_log CASCADE;
    """)
