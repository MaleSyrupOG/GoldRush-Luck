"""SECURITY DEFINER functions for the dispute workflow.

Revision ID: 0010_dw_dispute_fns
Revises: 0009_dw_cashier_fns
Create Date: 2026-05-01

Implements D/W design §3.3, §4.5:

- dw.open_dispute(p_ticket_type, p_ticket_uid, p_opener_id, p_opener_role, p_reason)
    Inserts a new dispute row with status='open' (UNIQUE on
    ticket_type+ticket_uid prevents two open disputes on the same
    ticket). Audit row.

- dw.resolve_dispute(p_dispute_id, p_action, p_amount, p_resolved_by)
    Resolves a dispute. Supported actions:
        'no-action'        — close as resolved with no monetary impact.
        'refund-full'      — when the dispute is on a confirmed withdraw
                              and we want to refund the user, calls
                              cancel_withdraw under the hood; the ticket
                              must already be in a state where this is
                              possible.
        'force-confirm'    — when the dispute is on a cancelled deposit
                              that the cashier confirms was completed in
                              game; closes the dispute as resolved.
        'partial-refund'   — sends `p_amount` G from treasury to the
                              user (uses dw.treasury_withdraw_to_user
                              defined in 0011).
"""

from alembic import op


revision = "0010_dw_dispute_fns"
down_revision = "0009_dw_cashier_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.open_dispute(
        p_ticket_type TEXT,
        p_ticket_uid  TEXT,
        p_opener_id   BIGINT,
        p_opener_role TEXT,
        p_reason      TEXT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_id    BIGINT;
        v_user  BIGINT;
    BEGIN
        IF p_ticket_type NOT IN ('deposit','withdraw') THEN
            RAISE EXCEPTION 'invalid_ticket_type';
        END IF;
        IF p_opener_role NOT IN ('admin','user','system') THEN
            RAISE EXCEPTION 'invalid_opener_role';
        END IF;

        IF p_ticket_type = 'deposit' THEN
            SELECT discord_id INTO v_user FROM dw.deposit_tickets WHERE ticket_uid = p_ticket_uid;
        ELSE
            SELECT discord_id INTO v_user FROM dw.withdraw_tickets WHERE ticket_uid = p_ticket_uid;
        END IF;
        IF v_user IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;

        INSERT INTO dw.disputes (
            ticket_type, ticket_uid, opener_id, opener_role, reason, status
        ) VALUES (
            p_ticket_type, p_ticket_uid, p_opener_id, p_opener_role, p_reason, 'open'
        ) RETURNING id INTO v_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := p_opener_role,
            p_actor_id       := p_opener_id,
            p_target_id      := v_user,
            p_action         := 'dispute_opened',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := p_reason,
            p_ref_type       := 'dispute',
            p_ref_id         := v_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'ticket_type', p_ticket_type,
                'ticket_uid',  p_ticket_uid
            )
        );

        RETURN v_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.open_dispute(TEXT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.open_dispute(TEXT, TEXT, BIGINT, TEXT, TEXT) TO goldrush_dw;
    """)

    # resolve_dispute calls dw.treasury_withdraw_to_user for partial-refund;
    # that function is created in 0011. Postgres resolves function names
    # at call time, not at CREATE FUNCTION time, so order does not matter
    # as long as the function exists when resolve_dispute runs at runtime.
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.resolve_dispute(
        p_dispute_id  BIGINT,
        p_action      TEXT,
        p_amount      BIGINT,
        p_resolved_by BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_dispute  dw.disputes%ROWTYPE;
        v_user     BIGINT;
    BEGIN
        SELECT * INTO v_dispute
        FROM dw.disputes
        WHERE id = p_dispute_id
        FOR UPDATE;
        IF v_dispute IS NULL THEN RAISE EXCEPTION 'dispute_not_found'; END IF;
        IF v_dispute.status IN ('resolved','rejected') THEN
            RAISE EXCEPTION 'dispute_already_terminal (%)', v_dispute.status;
        END IF;
        IF p_action NOT IN ('no-action','refund-full','force-confirm','partial-refund') THEN
            RAISE EXCEPTION 'invalid_action (%)', p_action;
        END IF;

        IF v_dispute.ticket_type = 'deposit' THEN
            SELECT discord_id INTO v_user FROM dw.deposit_tickets WHERE ticket_uid = v_dispute.ticket_uid;
        ELSE
            SELECT discord_id INTO v_user FROM dw.withdraw_tickets WHERE ticket_uid = v_dispute.ticket_uid;
        END IF;

        IF p_action = 'partial-refund' THEN
            IF p_amount IS NULL OR p_amount <= 0 THEN
                RAISE EXCEPTION 'partial_refund_requires_positive_amount';
            END IF;
            PERFORM dw.treasury_withdraw_to_user(p_amount, v_user, p_resolved_by, format('partial-refund dispute=%s', p_dispute_id));
        ELSIF p_action = 'refund-full' THEN
            -- Only meaningful for confirmed withdraws — admins call this
            -- when the user did not receive their gold and we accept that.
            IF v_dispute.ticket_type <> 'withdraw' THEN
                RAISE EXCEPTION 'refund_full_only_for_withdraw_disputes';
            END IF;
            DECLARE
                v_amount BIGINT;
            BEGIN
                SELECT amount INTO v_amount FROM dw.withdraw_tickets WHERE ticket_uid = v_dispute.ticket_uid;
                PERFORM dw.treasury_withdraw_to_user(v_amount, v_user, p_resolved_by,
                                                    format('refund-full dispute=%s', p_dispute_id));
            END;
        END IF;
        -- 'no-action' and 'force-confirm' do not move money.

        UPDATE dw.disputes
           SET status      = 'resolved',
               resolution  = p_action,
               resolved_by = p_resolved_by,
               resolved_at = NOW()
         WHERE id = p_dispute_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_resolved_by,
            p_target_id      := v_user,
            p_action         := 'dispute_resolved_' || p_action,
            p_amount         := p_amount,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := format('Dispute #%s resolved as %s', p_dispute_id, p_action),
            p_ref_type       := 'dispute',
            p_ref_id         := p_dispute_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('ticket_type', v_dispute.ticket_type, 'ticket_uid', v_dispute.ticket_uid)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.resolve_dispute(BIGINT, TEXT, BIGINT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.resolve_dispute(BIGINT, TEXT, BIGINT, BIGINT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.resolve_dispute(BIGINT, TEXT, BIGINT, BIGINT);
        DROP FUNCTION IF EXISTS dw.open_dispute(TEXT, TEXT, BIGINT, TEXT, TEXT);
    """)
