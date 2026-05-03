"""SECURITY DEFINER functions for the deposit lifecycle.

Revision ID: 0006_dw_deposit_fns
Revises: 0005_dw_disputes_embeds_config
Create Date: 2026-05-01

Implements D/W design §3.3 functions:

- dw.create_deposit_ticket(p_discord_id, p_char, p_realm, p_region,
        p_faction, p_amount, p_thread_id, p_parent_channel_id)
    Validates amount against the dw.global_config bounds, generates a
    ticket_uid, inserts a row in dw.deposit_tickets with status='open',
    writes an audit row. **No balance change** (the gold has not entered
    the system yet).

- dw.confirm_deposit(p_ticket_uid, p_cashier_id)
    Idempotent. Reads the ticket FOR UPDATE. Validates state (must be
    'claimed' by the same cashier). Creates the user's row in core.users
    on first deposit. Locks core.balances FOR UPDATE, credits the amount,
    bumps version. Sets ticket to 'confirmed'. Writes audit row. Updates
    cashier_stats. Returns the new balance.

- dw.cancel_deposit(p_ticket_uid, p_actor_id, p_reason)
    Closes a ticket without crediting. Audit row only. No balance change.

All three functions are owned by deathroll_admin (the migration role) and
granted EXECUTE only to deathroll_dw — bot code must call them; nothing
else can mint balance.
"""

from alembic import op


revision = "0006_dw_deposit_fns"
down_revision = "0005_dw_disputes_embeds_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- create_deposit_ticket ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.create_deposit_ticket(
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
        v_min       BIGINT;
        v_max       BIGINT;
        v_uid       TEXT;
        v_expires   TIMESTAMPTZ;
        v_ttl_s     BIGINT;
    BEGIN
        SELECT value_int INTO v_min FROM dw.global_config WHERE key = 'min_deposit_g';
        SELECT value_int INTO v_max FROM dw.global_config WHERE key = 'max_deposit_g';
        SELECT value_int INTO v_ttl_s FROM dw.global_config WHERE key = 'ticket_expiry_open_s';

        IF v_min IS NULL OR v_max IS NULL OR v_ttl_s IS NULL THEN
            RAISE EXCEPTION 'global_config missing required deposit keys';
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

        v_expires := NOW() + (v_ttl_s || ' seconds')::INTERVAL;
        v_uid := 'deposit-' || nextval('dw.deposit_tickets_id_seq');

        INSERT INTO dw.deposit_tickets (
            ticket_uid, discord_id, char_name, realm, region, faction, amount,
            status, thread_id, parent_channel_id, expires_at
        ) VALUES (
            v_uid, p_discord_id, p_char, p_realm, p_region, p_faction, p_amount,
            'open', p_thread_id, p_parent_channel_id, v_expires
        );

        -- Audit (no balance change yet so before/after are equal; we read
        -- the user's current balance if the user exists, else 0).
        DECLARE
            v_balance BIGINT := 0;
        BEGIN
            SELECT balance INTO v_balance FROM core.balances WHERE discord_id = p_discord_id;
            IF v_balance IS NULL THEN v_balance := 0; END IF;

            PERFORM core.audit_log_insert_with_chain(
                p_actor_type     := 'user',
                p_actor_id       := p_discord_id,
                p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = p_discord_id), 0),
                p_action         := 'deposit_ticket_opened',
                p_amount         := p_amount,
                p_balance_before := v_balance,
                p_balance_after  := v_balance,
                p_reason         := format('Deposit ticket opened for %s on %s-%s', p_char, p_realm, p_region),
                p_ref_type       := 'deposit_ticket',
                p_ref_id         := v_uid,
                p_bot_name       := 'dw',
                p_metadata       := jsonb_build_object(
                    'char',    p_char,
                    'realm',   p_realm,
                    'region',  p_region,
                    'faction', p_faction
                )
            );
        END;

        RETURN v_uid;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.create_deposit_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.create_deposit_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) TO deathroll_dw;
    """)

    # ---------- confirm_deposit ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.confirm_deposit(
        p_ticket_uid TEXT,
        p_cashier_id BIGINT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_ticket    dw.deposit_tickets%ROWTYPE;
        v_before    BIGINT;
        v_after     BIGINT;
    BEGIN
        SELECT * INTO v_ticket
        FROM dw.deposit_tickets
        WHERE ticket_uid = p_ticket_uid
        FOR UPDATE;
        IF v_ticket IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_ticket.status = 'confirmed' AND v_ticket.claimed_by = p_cashier_id THEN
            -- Idempotent retry: return current balance.
            SELECT balance INTO v_after FROM core.balances WHERE discord_id = v_ticket.discord_id;
            RETURN v_after;
        END IF;
        IF v_ticket.status <> 'claimed' THEN
            RAISE EXCEPTION 'ticket_not_claimed (status=%)', v_ticket.status;
        END IF;
        IF v_ticket.claimed_by <> p_cashier_id THEN
            RAISE EXCEPTION 'wrong_cashier (claimed_by=% calling=%)', v_ticket.claimed_by, p_cashier_id;
        END IF;

        -- Idempotent user creation. The first deposit ever for this user
        -- creates both the users row and the balances row.
        INSERT INTO core.users (discord_id) VALUES (v_ticket.discord_id)
            ON CONFLICT (discord_id) DO NOTHING;
        INSERT INTO core.balances (discord_id, balance) VALUES (v_ticket.discord_id, 0)
            ON CONFLICT (discord_id) DO NOTHING;

        SELECT balance INTO v_before
        FROM core.balances
        WHERE discord_id = v_ticket.discord_id
        FOR UPDATE;

        v_after := v_before + v_ticket.amount;

        UPDATE core.balances
           SET balance     = v_after,
               updated_at  = NOW(),
               version     = version + 1
         WHERE discord_id  = v_ticket.discord_id;

        UPDATE dw.deposit_tickets
           SET status = 'confirmed', confirmed_at = NOW(),
               last_activity_at = NOW()
         WHERE id = v_ticket.id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_cashier_id,
            p_target_id      := v_ticket.discord_id,
            p_action         := 'deposit_confirmed',
            p_amount         := v_ticket.amount,
            p_balance_before := v_before,
            p_balance_after  := v_after,
            p_reason         := format('Deposit confirmed by cashier %s for char %s on %s-%s',
                                        p_cashier_id, v_ticket.char_name, v_ticket.realm, v_ticket.region),
            p_ref_type       := 'deposit_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'char',    v_ticket.char_name,
                'realm',   v_ticket.realm,
                'region',  v_ticket.region,
                'faction', v_ticket.faction
            )
        );

        -- Update cashier stats (idempotent UPSERT).
        INSERT INTO dw.cashier_stats (discord_id, deposits_completed, total_volume_g, last_active_at, updated_at)
        VALUES (p_cashier_id, 1, v_ticket.amount, NOW(), NOW())
        ON CONFLICT (discord_id) DO UPDATE SET
            deposits_completed = dw.cashier_stats.deposits_completed + 1,
            total_volume_g     = dw.cashier_stats.total_volume_g + EXCLUDED.total_volume_g,
            last_active_at     = NOW(),
            updated_at         = NOW();

        RETURN v_after;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.confirm_deposit(TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.confirm_deposit(TEXT, BIGINT) TO deathroll_dw;
    """)

    # ---------- cancel_deposit ----------
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.cancel_deposit(
        p_ticket_uid TEXT,
        p_actor_id   BIGINT,
        p_reason     TEXT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_ticket    dw.deposit_tickets%ROWTYPE;
        v_balance   BIGINT := 0;
    BEGIN
        SELECT * INTO v_ticket
        FROM dw.deposit_tickets
        WHERE ticket_uid = p_ticket_uid
        FOR UPDATE;
        IF v_ticket IS NULL THEN RAISE EXCEPTION 'ticket_not_found'; END IF;
        IF v_ticket.status IN ('confirmed','cancelled','expired') THEN
            RAISE EXCEPTION 'ticket_already_terminal (status=%)', v_ticket.status;
        END IF;

        UPDATE dw.deposit_tickets
           SET status        = 'cancelled',
               cancelled_at  = NOW(),
               cancel_reason = p_reason,
               last_activity_at = NOW()
         WHERE id = v_ticket.id;

        -- Try to read existing balance for context; default 0 if user does not exist.
        SELECT balance INTO v_balance FROM core.balances WHERE discord_id = v_ticket.discord_id;
        IF v_balance IS NULL THEN v_balance := 0; END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := CASE WHEN p_actor_id = v_ticket.discord_id THEN 'user' ELSE 'cashier' END,
            p_actor_id       := p_actor_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = v_ticket.discord_id), 0),
            p_action         := 'deposit_cancelled',
            p_amount         := v_ticket.amount,
            p_balance_before := v_balance,
            p_balance_after  := v_balance,
            p_reason         := COALESCE(p_reason, 'cancelled'),
            p_ref_type       := 'deposit_ticket',
            p_ref_id         := p_ticket_uid,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('char', v_ticket.char_name, 'realm', v_ticket.realm)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.cancel_deposit(TEXT, BIGINT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.cancel_deposit(TEXT, BIGINT, TEXT) TO deathroll_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.cancel_deposit(TEXT, BIGINT, TEXT);
        DROP FUNCTION IF EXISTS dw.confirm_deposit(TEXT, BIGINT);
        DROP FUNCTION IF EXISTS dw.create_deposit_ticket(
            BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
        );
    """)
