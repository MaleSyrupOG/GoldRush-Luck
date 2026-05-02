"""SECURITY DEFINER fn for the cashier-idle worker (Story 8.3).

Revision ID: 0016_dw_expire_cashier
Revises: 0015_dw_deposit_ban_check
Create Date: 2026-05-03

Story 8.3 says: every 5 min, every cashier in ``status='online'``
who has been idle (``last_active_at < NOW() - 1 h``) is auto-set
offline AND their open ``cashier_sessions`` row gets
``end_reason='expired'`` (distinct from ``manual_offline`` which
``set_cashier_status`` writes when a human triggers the transition).

The existing ``set_cashier_status`` SDF hardcodes ``end_reason``
based on the destination status, so it can't write 'expired'.
``expire_cashier`` is a single-purpose verb — keeps the audit trail
clean (``cashier_status_offline_expired`` action vs.
``cashier_status_offline``) and lets the worker stay a thin wrapper.
"""

from alembic import op

revision = "0016_dw_expire_cashier"
down_revision = "0015_dw_deposit_ban_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.expire_cashier(
        p_discord_id BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_old_status TEXT;
    BEGIN
        SELECT status INTO v_old_status
        FROM dw.cashier_status
        WHERE discord_id = p_discord_id
        FOR UPDATE;

        IF v_old_status IS NULL OR v_old_status <> 'online' THEN
            -- Already offline / on break — desired state. Caller
            -- (the worker) swallows this so a race with manual
            -- /cashier-offline doesn't break the loop.
            RAISE EXCEPTION 'cashier_not_online';
        END IF;

        UPDATE dw.cashier_status
           SET status         = 'offline',
               set_at         = NOW(),
               last_active_at = NOW()
         WHERE discord_id = p_discord_id;

        UPDATE dw.cashier_sessions
           SET ended_at   = NOW(),
               duration_s = EXTRACT(EPOCH FROM (NOW() - started_at))::BIGINT,
               end_reason = 'expired'
         WHERE discord_id = p_discord_id
           AND ended_at IS NULL;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'system',
            p_actor_id       := 0,
            p_target_id      := p_discord_id,
            p_action         := 'cashier_status_offline_expired',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := 'idle for >1h — auto-offline by cashier_idle_worker',
            p_ref_type       := 'cashier_status',
            p_ref_id          := p_discord_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('previous', v_old_status)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.expire_cashier(BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.expire_cashier(BIGINT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.expire_cashier(BIGINT);
    """)
