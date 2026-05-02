"""Block banned users from opening deposit tickets (Story 9.3 AC).

Revision ID: 0015_dw_deposit_ban_check
Revises: 0014_dw_disputes_message_id
Create Date: 2026-05-03

Story 9.3 says: "After ban, banned user's /deposit and /withdraw
invocations rejected with ephemeral 'blacklisted' embed."

The withdraw side already enforces this in
``dw.create_withdraw_ticket`` (migration 0007 reads ``core.users.banned``
and raises ``user_banned``). The deposit side did NOT — it would
silently allow a banned user to open a deposit ticket and then the
gate kicks in only at confirm time, which is too late: the cashier
has already invested time engaging with the user.

This migration brings parity by replacing ``dw.create_deposit_ticket``
with a version that checks ``core.users.banned`` early. The ban check
runs BEFORE the row insert so we don't pollute the audit log with
``deposit_ticket_opened`` rows for blocked tickets. The Python
translation layer (``goldrush_core.balance.exceptions``) already
maps the ``user_banned`` sentinel to ``UserBanned`` and the deposit
orchestration returns ``DepositOutcome.UserBanned`` on that path —
the cog's existing ``_format_deposit_failure`` then renders the
"blacklisted" copy demanded by spec §6.4.

NOTE: ``core.users`` may not have a row for the user yet — first-ever
deposits don't pre-create the row. We tolerate that by falling
through (no row → not banned). Admins can pre-emptively ban via
``/admin-ban-user`` which idempotently creates the user row first.
"""

from alembic import op

revision = "0015_dw_deposit_ban_check"
down_revision = "0014_dw_disputes_message_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        v_banned    BOOLEAN;
    BEGIN
        -- Story 9.3: bring parity with create_withdraw_ticket — banned
        -- users cannot open deposit tickets either. Tolerate
        -- never-registered users (no row → not banned).
        SELECT banned INTO v_banned FROM core.users WHERE discord_id = p_discord_id;
        IF v_banned IS TRUE THEN
            RAISE EXCEPTION 'user_banned';
        END IF;

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

    -- Re-grant in case CREATE OR REPLACE ever returns to defaults.
    REVOKE ALL ON FUNCTION dw.create_deposit_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.create_deposit_ticket(
        BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ) TO goldrush_dw;
    """)


def downgrade() -> None:
    # Restore the pre-Story-9.3 deposit fn (no banned check).
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
    """)
