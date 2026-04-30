"""SECURITY DEFINER functions for blacklisting (ban / unban).

Revision ID: 0012_dw_ban_fns
Revises: 0011_dw_treasury_fns
Create Date: 2026-05-01

Implements D/W design §3.3, §6.4:

- dw.ban_user(p_user_id, p_reason, p_admin_id)
    Sets core.users.banned=TRUE with the reason; audit row 'user_banned'.
- dw.unban_user(p_user_id, p_admin_id)
    Reverses the flag; audit row 'user_unbanned'.

Both functions ensure the user row exists (idempotent INSERT) so an
admin can pre-emptively ban an as-yet-unregistered Discord ID — useful
for known scammers identified across servers.
"""

from alembic import op


revision = "0012_dw_ban_fns"
down_revision = "0011_dw_treasury_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.ban_user(
        p_user_id  BIGINT,
        p_reason   TEXT,
        p_admin_id BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_was_banned BOOLEAN;
    BEGIN
        IF p_user_id = 0 THEN RAISE EXCEPTION 'cannot_ban_treasury'; END IF;

        INSERT INTO core.users (discord_id) VALUES (p_user_id)
            ON CONFLICT (discord_id) DO NOTHING;
        INSERT INTO core.balances (discord_id, balance) VALUES (p_user_id, 0)
            ON CONFLICT (discord_id) DO NOTHING;

        SELECT banned INTO v_was_banned FROM core.users WHERE discord_id = p_user_id FOR UPDATE;
        UPDATE core.users
           SET banned        = TRUE,
               banned_reason = p_reason,
               banned_at     = NOW(),
               updated_at    = NOW()
         WHERE discord_id = p_user_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := p_user_id,
            p_action         := 'user_banned',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := COALESCE(p_reason, 'banned'),
            p_ref_type       := 'user',
            p_ref_id         := p_user_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('was_already_banned', COALESCE(v_was_banned, false))
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.ban_user(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.ban_user(BIGINT, TEXT, BIGINT) TO goldrush_dw;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION dw.unban_user(
        p_user_id  BIGINT,
        p_admin_id BIGINT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    BEGIN
        UPDATE core.users
           SET banned        = FALSE,
               banned_reason = NULL,
               banned_at     = NULL,
               updated_at    = NOW()
         WHERE discord_id = p_user_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'user_not_registered';
        END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'admin',
            p_actor_id       := p_admin_id,
            p_target_id      := p_user_id,
            p_action         := 'user_unbanned',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := 'admin unban',
            p_ref_type       := 'user',
            p_ref_id         := p_user_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := '{}'::jsonb
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.unban_user(BIGINT, BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.unban_user(BIGINT, BIGINT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.unban_user(BIGINT, BIGINT);
        DROP FUNCTION IF EXISTS dw.ban_user(BIGINT, TEXT, BIGINT);
    """)
