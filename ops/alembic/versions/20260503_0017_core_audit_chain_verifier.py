"""SECURITY DEFINER fn for the audit chain verifier (Story 8.6).

Revision ID: 0017_core_audit_chain_verifier
Revises: 0016_dw_expire_cashier
Create Date: 2026-05-03

Story 8.6 says: every 6 h, walk ``core.audit_log`` recomputing each
row's HMAC against the chain key and the previous row's hash. On
break, alert. The bot's role does NOT have SELECT on
``core.audit_log`` (deliberate — read access stays gated to
goldrush_readonly), so the verifier itself runs as SECURITY DEFINER
with the migration role's privileges and exposes a thin RETURNS
TABLE the bot can call.

Returned shape:

- ``checked_count``     INTEGER — how many rows were re-hashed
                                  this call (capped by
                                  ``p_max_rows`` to keep one tick
                                  bounded).
- ``last_verified_id``  BIGINT  — id of the last row whose hash
                                  matched (advances on success).
- ``broken_at_id``      BIGINT  — id of the first row whose
                                  recomputed hash differed, or
                                  NULL if everything checks out.

The canonical content used for the HMAC must MATCH BIT-FOR-BIT what
``core.audit_log_insert_with_chain`` (migration 0002) uses, or
every row will appear broken. The two functions are kept literally
side-by-side in this migration's docstring as documentation: the
INSERT side composes the canonical from p_* parameters before NOW()
is locked into ts; the verify side reads the same fields back from
the persisted row. As long as ``ts`` round-trips through Postgres
without precision loss (both sides format via jsonb_build_object's
default TIMESTAMPTZ → ISO-8601 serialization), the bytes are
identical.
"""

from alembic import op

revision = "0017_core_audit_chain_verifier"
down_revision = "0016_dw_expire_cashier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION core.verify_audit_chain(
        p_from_id   BIGINT,
        p_max_rows  INTEGER DEFAULT 1000
    ) RETURNS TABLE(
        checked_count    INTEGER,
        last_verified_id BIGINT,
        broken_at_id     BIGINT
    )
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = core, pg_catalog AS $$
    DECLARE
        v_chain_key      BYTEA;
        v_prev_hash      BYTEA;
        v_row            core.audit_log%ROWTYPE;
        v_canonical      TEXT;
        v_expected_hash  BYTEA;
        v_count          INTEGER := 0;
        v_last_verified  BIGINT  := COALESCE(p_from_id, 0);
        v_broken_at      BIGINT  := NULL;
    BEGIN
        -- Reuse the same key the inserter uses. If the operator
        -- forgot to set it, fail loudly so the misconfiguration is
        -- visible (same approach as audit_log_insert_with_chain).
        BEGIN
            v_chain_key := decode(current_setting('app.audit_chain_key'), 'hex');
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'audit chain key not configured';
        END;
        IF v_chain_key IS NULL OR octet_length(v_chain_key) = 0 THEN
            RAISE EXCEPTION 'audit chain key is empty';
        END IF;

        -- Seed v_prev_hash with the previous row's row_hash (the one
        -- BEFORE p_from_id). For p_from_id = 0 (or no prior row),
        -- v_prev_hash stays NULL and the COALESCE in the HMAC matches
        -- the inserter's first-row behaviour.
        IF p_from_id IS NOT NULL AND p_from_id > 0 THEN
            SELECT row_hash INTO v_prev_hash
              FROM core.audit_log
             WHERE id = (
                SELECT MAX(id) FROM core.audit_log WHERE id < p_from_id
             );
        END IF;

        FOR v_row IN
            SELECT *
              FROM core.audit_log
             WHERE id >= COALESCE(p_from_id, 0)
             ORDER BY id ASC
             LIMIT p_max_rows
        LOOP
            -- Same canonical composition as audit_log_insert_with_chain.
            v_canonical := jsonb_build_object(
                'ts',             v_row.ts,
                'actor_type',     v_row.actor_type,
                'actor_id',       v_row.actor_id,
                'target_id',      v_row.target_id,
                'action',         v_row.action,
                'amount',         v_row.amount,
                'balance_before', v_row.balance_before,
                'balance_after',  v_row.balance_after,
                'reason',         v_row.reason,
                'ref_type',       v_row.ref_type,
                'ref_id',         v_row.ref_id,
                'bot_name',       v_row.bot_name,
                'metadata',       v_row.metadata
            )::text;

            v_expected_hash := public.hmac(
                COALESCE(
                    v_prev_hash,
                    decode('00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000', 'hex')
                ) || convert_to(v_canonical, 'UTF8'),
                v_chain_key,
                'sha256'::text
            );

            IF v_expected_hash IS DISTINCT FROM v_row.row_hash THEN
                v_broken_at := v_row.id;
                EXIT;
            END IF;

            v_prev_hash      := v_row.row_hash;
            v_last_verified  := v_row.id;
            v_count          := v_count + 1;
        END LOOP;

        RETURN QUERY SELECT v_count, v_last_verified, v_broken_at;
    END;
    $$;

    REVOKE ALL ON FUNCTION core.verify_audit_chain(BIGINT, INTEGER) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION core.verify_audit_chain(BIGINT, INTEGER) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS core.verify_audit_chain(BIGINT, INTEGER);
    """)
