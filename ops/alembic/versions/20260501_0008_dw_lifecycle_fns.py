"""SECURITY DEFINER functions for ticket lifecycle: claim and release.

Revision ID: 0008_dw_lifecycle_fns
Revises: 0007_dw_withdraw_fns
Create Date: 2026-05-01

Implements D/W design §3.3:

- dw.claim_ticket(p_ticket_type, p_ticket_uid, p_cashier_id)
    Validates the cashier has at least one active character in the
    ticket's region (faction is not a filter — modern retail allows
    cross-faction trading). Atomically transitions the ticket from
    'open' to 'claimed' with claimed_by and claimed_at populated.
    Audit row.

- dw.release_ticket(p_ticket_type, p_ticket_uid, p_cashier_id)
    The current claimer can release the ticket back to 'open'. Used by
    /release and by the claim-idle worker after 30 min of inactivity.
"""

from alembic import op


revision = "0008_dw_lifecycle_fns"
down_revision = "0007_dw_withdraw_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.claim_ticket(
        p_ticket_type TEXT,
        p_ticket_uid  TEXT,
        p_cashier_id  BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_region    TEXT;
        v_status    TEXT;
        v_user_id   BIGINT;
        v_amount    BIGINT;
        v_match     BOOLEAN;
        v_balance   BIGINT;
    BEGIN
        IF p_ticket_type NOT IN ('deposit','withdraw') THEN
            RAISE EXCEPTION 'invalid_ticket_type';
        END IF;

        IF p_ticket_type = 'deposit' THEN
            SELECT region, status, discord_id, amount
              INTO v_region, v_status, v_user_id, v_amount
              FROM dw.deposit_tickets
              WHERE ticket_uid = p_ticket_uid
              FOR UPDATE;
        ELSE
            SELECT region, status, discord_id, amount
              INTO v_region, v_status, v_user_id, v_amount
              FROM dw.withdraw_tickets
              WHERE ticket_uid = p_ticket_uid
              FOR UPDATE;
        END IF;

        IF v_status IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_status <> 'open' THEN RAISE EXCEPTION 'already_claimed (status=%)', v_status; END IF;

        SELECT EXISTS (
            SELECT 1 FROM dw.cashier_characters
             WHERE discord_id = p_cashier_id
               AND region     = v_region
               AND is_active  = TRUE
        ) INTO v_match;
        IF NOT v_match THEN
            RAISE EXCEPTION 'region_mismatch (cashier % has no active char in region %)',
                p_cashier_id, v_region;
        END IF;

        IF p_ticket_type = 'deposit' THEN
            UPDATE dw.deposit_tickets
               SET status = 'claimed', claimed_by = p_cashier_id,
                   claimed_at = NOW(), last_activity_at = NOW()
             WHERE ticket_uid = p_ticket_uid;
        ELSE
            UPDATE dw.withdraw_tickets
               SET status = 'claimed', claimed_by = p_cashier_id,
                   claimed_at = NOW(), last_activity_at = NOW()
             WHERE ticket_uid = p_ticket_uid;
        END IF;

        SELECT balance INTO v_balance FROM core.balances WHERE discord_id = v_user_id;
        IF v_balance IS NULL THEN v_balance := 0; END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_cashier_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = v_user_id), 0),
            p_action         := p_ticket_type || '_claimed',
            p_amount         := v_amount,
            p_balance_before := v_balance,
            p_balance_after  := v_balance,
            p_reason         := format('Cashier %s claimed %s ticket', p_cashier_id, p_ticket_type),
            p_ref_type       := p_ticket_type || '_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('region', v_region)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.claim_ticket(TEXT, TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.claim_ticket(TEXT, TEXT, BIGINT) TO deathroll_dw;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION dw.release_ticket(
        p_ticket_type TEXT,
        p_ticket_uid  TEXT,
        p_actor_id    BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_status    TEXT;
        v_claimer   BIGINT;
        v_user_id   BIGINT;
        v_amount    BIGINT;
        v_balance   BIGINT;
    BEGIN
        IF p_ticket_type NOT IN ('deposit','withdraw') THEN
            RAISE EXCEPTION 'invalid_ticket_type';
        END IF;

        IF p_ticket_type = 'deposit' THEN
            SELECT status, claimed_by, discord_id, amount
              INTO v_status, v_claimer, v_user_id, v_amount
              FROM dw.deposit_tickets
              WHERE ticket_uid = p_ticket_uid
              FOR UPDATE;
        ELSE
            SELECT status, claimed_by, discord_id, amount
              INTO v_status, v_claimer, v_user_id, v_amount
              FROM dw.withdraw_tickets
              WHERE ticket_uid = p_ticket_uid
              FOR UPDATE;
        END IF;
        IF v_status IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_status <> 'claimed' THEN RAISE EXCEPTION 'ticket_not_claimed (status=%)', v_status; END IF;
        IF v_claimer <> p_actor_id THEN
            RAISE EXCEPTION 'wrong_cashier (claimed_by=% calling=%)', v_claimer, p_actor_id;
        END IF;

        IF p_ticket_type = 'deposit' THEN
            UPDATE dw.deposit_tickets
               SET status = 'open', claimed_by = NULL, claimed_at = NULL,
                   last_activity_at = NOW()
             WHERE ticket_uid = p_ticket_uid;
        ELSE
            UPDATE dw.withdraw_tickets
               SET status = 'open', claimed_by = NULL, claimed_at = NULL,
                   last_activity_at = NOW()
             WHERE ticket_uid = p_ticket_uid;
        END IF;

        SELECT balance INTO v_balance FROM core.balances WHERE discord_id = v_user_id;
        IF v_balance IS NULL THEN v_balance := 0; END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_actor_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = v_user_id), 0),
            p_action         := p_ticket_type || '_released',
            p_amount         := v_amount,
            p_balance_before := v_balance,
            p_balance_after  := v_balance,
            p_reason         := format('Cashier %s released the ticket', p_actor_id),
            p_ref_type       := p_ticket_type || '_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := '{}'::jsonb
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.release_ticket(TEXT, TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.release_ticket(TEXT, TEXT, BIGINT) TO deathroll_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.release_ticket(TEXT, TEXT, BIGINT);
        DROP FUNCTION IF EXISTS dw.claim_ticket(TEXT, TEXT, BIGINT);
    """)
