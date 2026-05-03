"""SECURITY DEFINER fn for ``/admin-view-audit`` (Story 10.8).

Revision ID: 0018_core_list_audit_events
Revises: 0017_core_audit_chain_verifier
Create Date: 2026-05-03

Story 10.8 says: ``/admin view-audit`` filters by ``target_id`` (user)
or returns the last N rows. The bot's ``deathroll_dw`` role does NOT
have SELECT on ``core.audit_log`` (deliberate — read access stays
gated to ``deathroll_readonly``), so the cog calls a SECURITY DEFINER
fn that runs with admin privileges.

Cap on rows: the SDF clamps ``p_limit`` to ``[1, 100]`` so a typo at
the slash command can't pull a million rows. 100 is plenty for an
ephemeral embed (Discord's per-embed cap is 25 fields by 1024 chars
each, ~25 KB, so 100 rows would actually overflow — but the cog
limits its embed render to 25 rows; the SDF cap is the safety net).
"""

from alembic import op

revision = "0018_core_list_audit_events"
down_revision = "0017_core_audit_chain_verifier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION core.list_audit_events(
        p_target_id BIGINT,
        p_limit     INTEGER DEFAULT 25
    ) RETURNS TABLE(
        id           BIGINT,
        ts           TIMESTAMPTZ,
        actor_type   TEXT,
        actor_id     BIGINT,
        target_id    BIGINT,
        action       TEXT,
        amount       BIGINT,
        reason       TEXT,
        bot_name     TEXT
    )
    LANGUAGE sql SECURITY DEFINER
    SET search_path = core, pg_catalog AS $$
        SELECT id, ts, actor_type, actor_id, target_id, action,
               amount, reason, bot_name
          FROM core.audit_log
         WHERE p_target_id IS NULL OR target_id = p_target_id
         ORDER BY ts DESC
         LIMIT LEAST(GREATEST(COALESCE(p_limit, 25), 1), 100);
    $$;

    REVOKE ALL ON FUNCTION core.list_audit_events(BIGINT, INTEGER) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION core.list_audit_events(BIGINT, INTEGER) TO deathroll_dw;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS core.list_audit_events(BIGINT, INTEGER);
    """)
