"""dw.disputes, dw.dynamic_embeds, dw.global_config (with seed values).

Revision ID: 0005_dw_disputes_embeds_config
Revises: 0004_dw_cashier_tables
Create Date: 2026-05-01

Implements D/W design §3.2.

global_config seed values reflect the locked v1 economic decisions:
    min_deposit_g           200
    max_deposit_g           200000
    min_withdraw_g          1000
    max_withdraw_g          200000
    withdraw_fee_bps        200    (2 %)
    deposit_fee_bps         0
    daily_user_limit_g      0      (disabled)
    ticket_expiry_open_s    86400  (24 h)
    ticket_repinging_s      3600   (1 h)
    ticket_claim_idle_s     1800   (30 min)
    ticket_claim_expiry_s   7200   (2 h)
    cashier_auto_offline_s  3600   (1 h)
"""

from alembic import op


revision = "0005_dw_disputes_embeds_config"
down_revision = "0004_dw_cashier_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dw.disputes (
            id                  BIGSERIAL   PRIMARY KEY,
            ticket_type         TEXT        NOT NULL
                                            CHECK (ticket_type IN ('deposit','withdraw')),
            ticket_uid          TEXT        NOT NULL,
            opener_id           BIGINT      NOT NULL,
            opener_role         TEXT        NOT NULL
                                            CHECK (opener_role IN ('admin','user','system')),
            reason              TEXT        NOT NULL,
            status              TEXT        NOT NULL
                                            CHECK (status IN ('open','investigating','resolved','rejected')),
            resolution          TEXT,
            resolved_by         BIGINT,
            resolved_at         TIMESTAMPTZ,
            opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (ticket_type, ticket_uid)
        );

        CREATE INDEX idx_disputes_status ON dw.disputes (status, opened_at DESC);
    """)

    op.execute("""
        CREATE TABLE dw.dynamic_embeds (
            embed_key       TEXT        PRIMARY KEY,
            channel_id      BIGINT      NOT NULL,
            message_id      BIGINT,
            title           TEXT        NOT NULL,
            description     TEXT        NOT NULL,
            color_hex       TEXT        NOT NULL DEFAULT '#F2B22A',
            fields          JSONB       NOT NULL DEFAULT '[]'::jsonb,
            image_url       TEXT,
            footer_text     TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by      BIGINT      NOT NULL
        );
    """)

    op.execute("""
        CREATE TABLE dw.global_config (
            key             TEXT        PRIMARY KEY,
            value_int       BIGINT,
            value_text      TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by      BIGINT      NOT NULL
        );
    """)

    # Seed config — constants reflect the locked v1 decisions.
    # updated_by = 0 means "system / migration".
    op.execute("""
        INSERT INTO dw.global_config (key, value_int, updated_by) VALUES
            ('min_deposit_g',          200,    0),
            ('max_deposit_g',          200000, 0),
            ('min_withdraw_g',         1000,   0),
            ('max_withdraw_g',         200000, 0),
            ('withdraw_fee_bps',       200,    0),
            ('deposit_fee_bps',        0,      0),
            ('daily_user_limit_g',     0,      0),
            ('ticket_expiry_open_s',   86400,  0),
            ('ticket_repinging_s',     3600,   0),
            ('ticket_claim_idle_s',    1800,   0),
            ('ticket_claim_expiry_s',  7200,   0),
            ('cashier_auto_offline_s', 3600,   0)
        ON CONFLICT (key) DO NOTHING;
    """)

    op.execute("""
        GRANT SELECT, INSERT, UPDATE
            ON dw.disputes, dw.dynamic_embeds, dw.global_config
            TO goldrush_dw;
        GRANT USAGE, SELECT ON SEQUENCE dw.disputes_id_seq TO goldrush_dw;
        GRANT SELECT
            ON dw.disputes, dw.dynamic_embeds, dw.global_config
            TO goldrush_readonly;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS dw.global_config   CASCADE;
        DROP TABLE IF EXISTS dw.dynamic_embeds  CASCADE;
        DROP TABLE IF EXISTS dw.disputes        CASCADE;
    """)
