"""SECURITY DEFINER function to reject (close-without-resolution) a dispute.

Revision ID: 0013_dw_dispute_reject_fn
Revises: 0012_dw_ban_fns
Create Date: 2026-05-03

Story 9.1 introduced ``/admin dispute reject`` as a distinct action from
``/admin dispute resolve``. The existing ``dw.resolve_dispute`` always
sets ``status='resolved'`` and routes through the action ladder
(no-action / partial-refund / refund-full / force-confirm), all of which
imply the admin sided with the opener. ``reject`` is the opposite verb:
the admin sided AGAINST the opener, no money moves, and the dispute
closes with ``status='rejected'``.

Splitting the verbs into two SQL fns keeps each one focused on a single
state transition and makes the audit log read cleanly: every dispute
either ends with ``dispute_resolved_<action>`` or ``dispute_rejected``.
"""

from alembic import op

revision = "0013_dw_dispute_reject_fn"
down_revision = "0012_dw_ban_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.reject_dispute(
        p_dispute_id BIGINT,
        p_reason     TEXT,
        p_admin_id   BIGINT
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

        -- Resolve the target user for the audit row. Tickets always exist
        -- because open_dispute proved their existence at insert time, but
        -- we tolerate a row deletion (admin force-cancel) by falling back
        -- to the opener.
        IF v_dispute.ticket_type = 'deposit' THEN
            SELECT discord_id INTO v_user FROM dw.deposit_tickets
                WHERE ticket_uid = v_dispute.ticket_uid;
        ELSE
            SELECT discord_id INTO v_user FROM dw.withdraw_tickets
                WHERE ticket_uid = v_dispute.ticket_uid;
        END IF;
        IF v_user IS NULL THEN v_user := v_dispute.opener_id; END IF;

        UPDATE dw.disputes
           SET status      = 'rejected',
               resolution  = p_reason,
               resolved_by = p_admin_id,
               resolved_at = NOW()
         WHERE id = p_dispute_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := v_user,
            p_action         := 'dispute_rejected',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := p_reason,
            p_ref_type       := 'dispute',
            p_ref_id         := p_dispute_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'ticket_type', v_dispute.ticket_type,
                'ticket_uid',  v_dispute.ticket_uid
            )
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.reject_dispute(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.reject_dispute(BIGINT, TEXT, BIGINT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.reject_dispute(BIGINT, TEXT, BIGINT);
    """)
