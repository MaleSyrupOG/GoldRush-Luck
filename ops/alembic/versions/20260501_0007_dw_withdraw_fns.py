"""SECURITY DEFINER functions for the withdraw lifecycle.

Revision ID: 0007_dw_withdraw_fns
Revises: 0006_dw_deposit_fns
Create Date: 2026-05-01

Implements D/W design §3.3:

- dw.create_withdraw_ticket(...)
    Validates the user is not banned, has enough balance, and the amount is
    within configured limits. Captures the current withdraw_fee_bps value
    on the row (so subsequent rate changes do not retroactively affect this
    ticket). Locks balance: balance -= amount, locked_balance += amount.
    Inserts the ticket and writes an audit row.

- dw.confirm_withdraw(p_ticket_uid, p_cashier_id)
    Finalises the lock as a deduction (locked_balance -= amount), credits
    the fee to the treasury (core.balances[discord_id=0]). Sets
    amount_delivered = amount - fee on the ticket. Audits and updates
    cashier_stats. Idempotent on retry.

- dw.cancel_withdraw(p_ticket_uid, p_actor_id, p_reason)
    REFUND. balance += amount, locked_balance -= amount. Audit row.

All three preserve the operational invariant that
    SUM(user balances) + treasury_balance + admin_swept_total
    = total ever deposited
across every code path (proven by the property test in tests/property/dw).
"""

from alembic import op


revision = "0007_dw_withdraw_fns"
down_revision = "0006_dw_deposit_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- create_withdraw_ticket ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.create_withdraw_ticket(
        p_discord_id        BIGINT,
        p_char              TEXT,
        p_realm             TEXT,
        p_region            TEXT,
        p_faction           TEXT,
        p_amount            BIGINT,
        p_thread_id         BIGINT,
        p_parent_channel_id BIGINT
    ) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_min      BIGINT;
        v_max      BIGINT;
        v_fee_bps  BIGINT;
        v_ttl_s    BIGINT;
        v_balance  BIGINT;
        v_locked   BIGINT;
        v_fee      BIGINT;
        v_uid      TEXT;
        v_expires  TIMESTAMPTZ;
        v_banned   BOOLEAN;
    BEGIN
        SELECT value_int INTO v_min     FROM dw.global_config WHERE key='min_withdraw_g';
        SELECT value_int INTO v_max     FROM dw.global_config WHERE key='max_withdraw_g';
        SELECT value_int INTO v_fee_bps FROM dw.global_config WHERE key='withdraw_fee_bps';
        SELECT value_int INTO v_ttl_s   FROM dw.global_config WHERE key='ticket_expiry_open_s';

        IF v_min IS NULL OR v_max IS NULL OR v_fee_bps IS NULL OR v_ttl_s IS NULL THEN
            RAISE EXCEPTION 'global_config missing required withdraw keys';
        END IF;
        IF p_amount < v_min OR p_amount > v_max THEN
            RAISE EXCEPTION 'amount_out_of_range (got %, expected % to %)',
                p_amount, v_min, v_max;
        END IF;
        IF p_region NOT IN ('EU','NA') THEN
            RAISE EXCEPTION 'invalid_region (%)', p_region;
        END IF;
        IF p_faction NOT IN ('Alliance','Horde') THEN
            RAISE EXCEPTION 'invalid_faction (%)', p_faction;
        END IF;

        SELECT banned INTO v_banned FROM core.users WHERE discord_id = p_discord_id FOR UPDATE;
        IF v_banned IS NULL THEN
            RAISE EXCEPTION 'user_not_registered';
        END IF;
        IF v_banned IS TRUE THEN
            RAISE EXCEPTION 'user_banned';
        END IF;

        SELECT balance, locked_balance
          INTO v_balance, v_locked
          FROM core.balances
          WHERE discord_id = p_discord_id
          FOR UPDATE;
        IF v_balance < p_amount THEN
            RAISE EXCEPTION 'insufficient_balance (have %, need %)', v_balance, p_amount;
        END IF;

        v_fee     := (p_amount * v_fee_bps) / 10000;
        v_expires := NOW() + (v_ttl_s || ' seconds')::INTERVAL;
        v_uid     := 'withdraw-' || nextval('dw.withdraw_tickets_id_seq');

        UPDATE core.balances
           SET balance        = balance - p_amount,
               locked_balance = locked_balance + p_amount,
               updated_at     = NOW(),
               version        = version + 1
         WHERE discord_id     = p_discord_id;

        INSERT INTO dw.withdraw_tickets (
            ticket_uid, discord_id, char_name, realm, region, faction, amount, fee,
            status, thread_id, parent_channel_id, expires_at
        ) VALUES (
            v_uid, p_discord_id, p_char, p_realm, p_region, p_faction, p_amount, v_fee,
            'open', p_thread_id, p_parent_channel_id, v_expires
        );

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'user',
            p_actor_id       := p_discord_id,
            p_target_id      := p_discord_id,
            p_action         := 'withdraw_locked',
            p_amount         := p_amount,
            p_balance_before := v_balance,
            p_balance_after  := v_balance - p_amount,
            p_reason         := format('Withdraw locked %s G (fee %s G); deliver %s G ingame to %s on %s-%s',
                                        p_amount, v_fee, p_amount - v_fee, p_char, p_realm, p_region),
            p_ref_type       := 'withdraw_ticket',
            p_ref_id         := v_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'char',    p_char,    'realm', p_realm,
                'region',  p_region,  'faction', p_faction,
                'fee_bps', v_fee_bps, 'fee', v_fee
            )
        );

        RETURN v_uid;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.create_withdraw_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.create_withdraw_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) TO goldrush_dw;
    """)

    # ---------- confirm_withdraw ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.confirm_withdraw(
        p_ticket_uid TEXT,
        p_cashier_id BIGINT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_ticket    dw.withdraw_tickets%ROWTYPE;
        v_before    BIGINT;
        v_locked    BIGINT;
        v_treasury_before BIGINT;
    BEGIN
        SELECT * INTO v_ticket
        FROM dw.withdraw_tickets
        WHERE ticket_uid = p_ticket_uid
        FOR UPDATE;
        IF v_ticket IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_ticket.status = 'confirmed' AND v_ticket.claimed_by = p_cashier_id THEN
            -- Idempotent retry.
            SELECT balance INTO v_before FROM core.balances WHERE discord_id = v_ticket.discord_id;
            RETURN v_before;
        END IF;
        IF v_ticket.status <> 'claimed' THEN
            RAISE EXCEPTION 'ticket_not_claimed (status=%)', v_ticket.status;
        END IF;
        IF v_ticket.claimed_by <> p_cashier_id THEN
            RAISE EXCEPTION 'wrong_cashier (claimed_by=% calling=%)', v_ticket.claimed_by, p_cashier_id;
        END IF;

        SELECT balance, locked_balance
          INTO v_before, v_locked
          FROM core.balances
          WHERE discord_id = v_ticket.discord_id
          FOR UPDATE;
        IF v_locked < v_ticket.amount THEN
            -- Should never happen unless data was tampered; refuse to proceed.
            RAISE EXCEPTION 'invariant_violation_locked_too_low (locked=%, ticket_amount=%)',
                v_locked, v_ticket.amount;
        END IF;

        -- Finalise the lock as a real deduction.
        UPDATE core.balances
           SET locked_balance = locked_balance - v_ticket.amount,
               updated_at     = NOW(),
               version        = version + 1
         WHERE discord_id     = v_ticket.discord_id;

        -- Credit treasury with the fee.
        SELECT balance INTO v_treasury_before FROM core.balances WHERE discord_id = 0 FOR UPDATE;
        UPDATE core.balances
           SET balance     = balance + v_ticket.fee,
               updated_at  = NOW(),
               version     = version + 1
         WHERE discord_id  = 0;

        UPDATE dw.withdraw_tickets
           SET status           = 'confirmed',
               confirmed_at     = NOW(),
               amount_delivered = v_ticket.amount - v_ticket.fee,
               last_activity_at = NOW()
         WHERE id = v_ticket.id;

        -- User-facing audit row.
        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_cashier_id,
            p_target_id      := v_ticket.discord_id,
            p_action         := 'withdraw_confirmed',
            p_amount         := v_ticket.amount,
            p_balance_before := v_before,
            p_balance_after  := v_before,
            p_reason         := format('Withdraw confirmed: %s G out, %s G delivered ingame, %s G fee',
                                        v_ticket.amount, v_ticket.amount - v_ticket.fee, v_ticket.fee),
            p_ref_type       := 'withdraw_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'char',             v_ticket.char_name,
                'realm',            v_ticket.realm,
                'region',           v_ticket.region,
                'faction',          v_ticket.faction,
                'fee',              v_ticket.fee,
                'amount_delivered', v_ticket.amount - v_ticket.fee
            )
        );

        -- Treasury credit audit row (target_id = 0, the system).
        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'system',
            p_actor_id       := 0,
            p_target_id      := 0,
            p_action         := 'treasury_fee_credit',
            p_amount         := v_ticket.fee,
            p_balance_before := v_treasury_before,
            p_balance_after  := v_treasury_before + v_ticket.fee,
            p_reason         := 'Withdraw fee accrued to treasury',
            p_ref_type       := 'withdraw_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('source_user', v_ticket.discord_id)
        );

        -- Cashier stats.
        INSERT INTO dw.cashier_stats (discord_id, withdraws_completed, total_volume_g, last_active_at, updated_at)
        VALUES (p_cashier_id, 1, v_ticket.amount, NOW(), NOW())
        ON CONFLICT (discord_id) DO UPDATE SET
            withdraws_completed = dw.cashier_stats.withdraws_completed + 1,
            total_volume_g      = dw.cashier_stats.total_volume_g + EXCLUDED.total_volume_g,
            last_active_at      = NOW(),
            updated_at          = NOW();

        RETURN v_before;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.confirm_withdraw(TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.confirm_withdraw(TEXT, BIGINT) TO goldrush_dw;
    """)

    # ---------- cancel_withdraw (REFUND path) ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.cancel_withdraw(
        p_ticket_uid TEXT,
        p_actor_id   BIGINT,
        p_reason     TEXT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_ticket    dw.withdraw_tickets%ROWTYPE;
        v_before    BIGINT;
        v_locked    BIGINT;
        v_after     BIGINT;
    BEGIN
        SELECT * INTO v_ticket
        FROM dw.withdraw_tickets
        WHERE ticket_uid = p_ticket_uid
        FOR UPDATE;
        IF v_ticket IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_ticket.status IN ('confirmed','cancelled','expired') THEN
            RAISE EXCEPTION 'ticket_already_terminal (status=%)', v_ticket.status;
        END IF;

        SELECT balance, locked_balance
          INTO v_before, v_locked
          FROM core.balances
          WHERE discord_id = v_ticket.discord_id
          FOR UPDATE;
        IF v_locked < v_ticket.amount THEN
            RAISE EXCEPTION 'invariant_violation_locked_too_low (locked=%, ticket_amount=%)',
                v_locked, v_ticket.amount;
        END IF;

        v_after := v_before + v_ticket.amount;

        UPDATE core.balances
           SET balance        = v_after,
               locked_balance = locked_balance - v_ticket.amount,
               updated_at     = NOW(),
               version        = version + 1
         WHERE discord_id     = v_ticket.discord_id;

        UPDATE dw.withdraw_tickets
           SET status           = 'cancelled',
               cancelled_at     = NOW(),
               cancel_reason    = p_reason,
               last_activity_at = NOW()
         WHERE id = v_ticket.id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := CASE WHEN p_actor_id = v_ticket.discord_id THEN 'user' ELSE 'cashier' END,
            p_actor_id       := p_actor_id,
            p_target_id      := v_ticket.discord_id,
            p_action         := 'withdraw_cancelled_refund',
            p_amount         := v_ticket.amount,
            p_balance_before := v_before,
            p_balance_after  := v_after,
            p_reason         := COALESCE(p_reason, 'cancelled'),
            p_ref_type       := 'withdraw_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('refunded', v_ticket.amount)
        );

        -- Cashier-side cancel hits stats; user-side does not.
        IF p_actor_id <> v_ticket.discord_id AND v_ticket.claimed_by IS NOT NULL THEN
            INSERT INTO dw.cashier_stats (discord_id, withdraws_cancelled, last_active_at, updated_at)
            VALUES (v_ticket.claimed_by, 1, NOW(), NOW())
            ON CONFLICT (discord_id) DO UPDATE SET
                withdraws_cancelled = dw.cashier_stats.withdraws_cancelled + 1,
                last_active_at      = NOW(),
                updated_at          = NOW();
        END IF;

        RETURN v_after;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.cancel_withdraw(TEXT, BIGINT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.cancel_withdraw(TEXT, BIGINT, TEXT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.cancel_withdraw(TEXT, BIGINT, TEXT);
        DROP FUNCTION IF EXISTS dw.confirm_withdraw(TEXT, BIGINT);
        DROP FUNCTION IF EXISTS dw.create_withdraw_ticket(
            BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
        );
    """)
