"""SECURITY DEFINER functions for treasury operations.

Revision ID: 0011_dw_treasury_fns
Revises: 0010_dw_dispute_fns
Create Date: 2026-05-01

Implements D/W design §3.3, §4.6, §6.2:

- dw.treasury_sweep(p_amount, p_admin_id, p_reason)
    Records that the admin physically removed `p_amount` G from the
    in-game guild bank. Debits core.balances[discord_id=0]. Writes
    audit row tagged 'treasury_swept'. **No other balance changes.**
    The actual gold movement happens in the game; the bot records it.

- dw.treasury_withdraw_to_user(p_amount, p_target, p_admin_id, p_reason)
    Moves `p_amount` G from the treasury account to a real user balance,
    e.g. as part of a dispute refund. Both deltas (treasury -, user +)
    are recorded; two audit rows are written.
"""

from alembic import op


revision = "0011_dw_treasury_fns"
down_revision = "0010_dw_dispute_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.treasury_sweep(
        p_amount   BIGINT,
        p_admin_id BIGINT,
        p_reason   TEXT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_before BIGINT;
        v_after  BIGINT;
    BEGIN
        IF p_amount IS NULL OR p_amount <= 0 THEN
            RAISE EXCEPTION 'amount_must_be_positive';
        END IF;

        SELECT balance INTO v_before
        FROM core.balances
        WHERE discord_id = 0
        FOR UPDATE;
        IF v_before IS NULL THEN RAISE EXCEPTION 'treasury_row_missing'; END IF;
        IF v_before < p_amount THEN
            RAISE EXCEPTION 'insufficient_treasury (have %, sweeping %)', v_before, p_amount;
        END IF;

        v_after := v_before - p_amount;

        UPDATE core.balances
           SET balance     = v_after,
               updated_at  = NOW(),
               version     = version + 1
         WHERE discord_id  = 0;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := 0,
            p_action         := 'treasury_swept',
            p_amount         := p_amount,
            p_balance_before := v_before,
            p_balance_after  := v_after,
            p_reason         := COALESCE(p_reason, 'admin treasury sweep'),
            p_ref_type       := 'treasury',
            p_ref_id         := 'sweep:' || extract(epoch from NOW())::BIGINT::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('admin_id', p_admin_id)
        );

        RETURN v_after;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.treasury_sweep(BIGINT, BIGINT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.treasury_sweep(BIGINT, BIGINT, TEXT) TO goldrush_dw;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION dw.treasury_withdraw_to_user(
        p_amount        BIGINT,
        p_target_user   BIGINT,
        p_admin_id      BIGINT,
        p_reason        TEXT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_treasury_before BIGINT;
        v_user_before     BIGINT;
        v_user_after      BIGINT;
    BEGIN
        IF p_amount IS NULL OR p_amount <= 0 THEN
            RAISE EXCEPTION 'amount_must_be_positive';
        END IF;
        IF p_target_user = 0 THEN
            RAISE EXCEPTION 'cannot_withdraw_to_treasury_self';
        END IF;

        SELECT balance INTO v_treasury_before FROM core.balances WHERE discord_id = 0 FOR UPDATE;
        IF v_treasury_before IS NULL THEN RAISE EXCEPTION 'treasury_row_missing'; END IF;
        IF v_treasury_before < p_amount THEN
            RAISE EXCEPTION 'insufficient_treasury (have %, sending %)', v_treasury_before, p_amount;
        END IF;

        -- Ensure target user exists.
        INSERT INTO core.users (discord_id) VALUES (p_target_user) ON CONFLICT DO NOTHING;
        INSERT INTO core.balances (discord_id, balance) VALUES (p_target_user, 0) ON CONFLICT DO NOTHING;

        SELECT balance INTO v_user_before FROM core.balances WHERE discord_id = p_target_user FOR UPDATE;
        v_user_after := v_user_before + p_amount;

        UPDATE core.balances
           SET balance = balance - p_amount, updated_at = NOW(), version = version + 1
         WHERE discord_id = 0;
        UPDATE core.balances
           SET balance = v_user_after, updated_at = NOW(), version = version + 1
         WHERE discord_id = p_target_user;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := 0,
            p_action         := 'treasury_to_user_debit',
            p_amount         := p_amount,
            p_balance_before := v_treasury_before,
            p_balance_after  := v_treasury_before - p_amount,
            p_reason         := COALESCE(p_reason, 'treasury → user'),
            p_ref_type       := 'treasury',
            p_ref_id         := 'to_user:' || p_target_user::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('target_user', p_target_user)
        );
        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := p_target_user,
            p_action         := 'treasury_to_user_credit',
            p_amount         := p_amount,
            p_balance_before := v_user_before,
            p_balance_after  := v_user_after,
            p_reason         := COALESCE(p_reason, 'treasury → user'),
            p_ref_type       := 'treasury',
            p_ref_id         := 'from_treasury:' || p_admin_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('admin_id', p_admin_id)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.treasury_withdraw_to_user(BIGINT, BIGINT, BIGINT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.treasury_withdraw_to_user(BIGINT, BIGINT, BIGINT, TEXT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.treasury_withdraw_to_user(BIGINT, BIGINT, BIGINT, TEXT);
        DROP FUNCTION IF EXISTS dw.treasury_sweep(BIGINT, BIGINT, TEXT);
    """)
