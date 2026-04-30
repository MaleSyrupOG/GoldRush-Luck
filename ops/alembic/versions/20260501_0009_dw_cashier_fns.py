"""SECURITY DEFINER functions for cashier management.

Revision ID: 0009_dw_cashier_fns
Revises: 0008_dw_lifecycle_fns
Create Date: 2026-05-01

Implements D/W design §3.3:

- dw.add_cashier_character — registers a new char (idempotent on the
  UNIQUE composite key).
- dw.remove_cashier_character — soft delete (is_active=false).
- dw.set_cashier_status — upserts the cashier's online/offline/break
  flag and manages the cashier_sessions row that tracks online time.
"""

from alembic import op


revision = "0009_dw_cashier_fns"
down_revision = "0008_dw_lifecycle_fns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION dw.add_cashier_character(
        p_discord_id BIGINT,
        p_char       TEXT,
        p_realm      TEXT,
        p_region     TEXT,
        p_faction    TEXT
    ) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_id BIGINT;
    BEGIN
        IF p_region NOT IN ('EU','NA') THEN RAISE EXCEPTION 'invalid_region'; END IF;
        IF p_faction NOT IN ('Alliance','Horde') THEN RAISE EXCEPTION 'invalid_faction'; END IF;

        INSERT INTO dw.cashier_characters (discord_id, char_name, realm, region, faction)
        VALUES (p_discord_id, p_char, p_realm, p_region, p_faction)
        ON CONFLICT (discord_id, char_name, realm, region) DO UPDATE
            SET is_active = TRUE, removed_at = NULL
        RETURNING id INTO v_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_discord_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = p_discord_id), 0),
            p_action         := 'cashier_addchar',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := format('Cashier registered char %s on %s-%s (%s)',
                                        p_char, p_realm, p_region, p_faction),
            p_ref_type       := 'cashier_character',
            p_ref_id         := v_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object(
                'char', p_char, 'realm', p_realm,
                'region', p_region, 'faction', p_faction
            )
        );

        RETURN v_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.add_cashier_character(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.add_cashier_character(BIGINT, TEXT, TEXT, TEXT, TEXT) TO goldrush_dw;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION dw.remove_cashier_character(
        p_discord_id BIGINT,
        p_char       TEXT,
        p_realm      TEXT,
        p_region     TEXT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_count INT;
    BEGIN
        UPDATE dw.cashier_characters
           SET is_active = FALSE, removed_at = NOW()
         WHERE discord_id = p_discord_id
           AND char_name  = p_char
           AND realm      = p_realm
           AND region     = p_region
           AND is_active  = TRUE;
        GET DIAGNOSTICS v_count = ROW_COUNT;
        IF v_count = 0 THEN RAISE EXCEPTION 'character_not_found_or_already_removed'; END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_discord_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = p_discord_id), 0),
            p_action         := 'cashier_removechar',
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := format('Cashier removed char %s on %s-%s', p_char, p_realm, p_region),
            p_ref_type       := 'cashier_character',
            p_ref_id         := NULL,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('char', p_char, 'realm', p_realm, 'region', p_region)
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.remove_cashier_character(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.remove_cashier_character(BIGINT, TEXT, TEXT, TEXT) TO goldrush_dw;
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION dw.set_cashier_status(
        p_discord_id BIGINT,
        p_status     TEXT
    ) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = dw, core, pg_catalog AS $$
    DECLARE
        v_old_status TEXT;
    BEGIN
        IF p_status NOT IN ('online','offline','break') THEN
            RAISE EXCEPTION 'invalid_status (%)', p_status;
        END IF;

        SELECT status INTO v_old_status
        FROM dw.cashier_status
        WHERE discord_id = p_discord_id
        FOR UPDATE;

        INSERT INTO dw.cashier_status (discord_id, status, set_at, last_active_at)
        VALUES (p_discord_id, p_status, NOW(), NOW())
        ON CONFLICT (discord_id) DO UPDATE
            SET status         = EXCLUDED.status,
                set_at         = EXCLUDED.set_at,
                last_active_at = EXCLUDED.last_active_at;

        -- Manage cashier_sessions:
        --   transition INTO online: open new session row
        --   transition OUT OF online (offline or break): close active session row
        IF p_status = 'online' AND (v_old_status IS NULL OR v_old_status <> 'online') THEN
            INSERT INTO dw.cashier_sessions (discord_id, started_at)
            VALUES (p_discord_id, NOW());
        ELSIF v_old_status = 'online' AND p_status <> 'online' THEN
            UPDATE dw.cashier_sessions
               SET ended_at   = NOW(),
                   duration_s = EXTRACT(EPOCH FROM (NOW() - started_at))::BIGINT,
                   end_reason = CASE p_status
                       WHEN 'offline' THEN 'manual_offline'
                       WHEN 'break'   THEN 'manual_break'
                       ELSE 'manual_offline'
                   END
             WHERE discord_id = p_discord_id
               AND ended_at IS NULL;
        END IF;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'cashier',
            p_actor_id       := p_discord_id,
            p_target_id      := COALESCE((SELECT discord_id FROM core.users WHERE discord_id = p_discord_id), 0),
            p_action         := 'cashier_status_' || p_status,
            p_amount         := NULL,
            p_balance_before := 0,
            p_balance_after  := 0,
            p_reason         := format('Cashier moved to %s', p_status),
            p_ref_type       := 'cashier_status',
            p_ref_id         := p_discord_id::TEXT,
            p_bot_name       := 'dw',
            p_metadata       := jsonb_build_object('previous', COALESCE(v_old_status, 'never'))
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION dw.set_cashier_status(BIGINT, TEXT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION dw.set_cashier_status(BIGINT, TEXT) TO goldrush_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS dw.set_cashier_status(BIGINT, TEXT);
        DROP FUNCTION IF EXISTS dw.remove_cashier_character(BIGINT, TEXT, TEXT, TEXT);
        DROP FUNCTION IF EXISTS dw.add_cashier_character(BIGINT, TEXT, TEXT, TEXT, TEXT);
    """)
