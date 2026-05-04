"""luck.consume_rate_token + fairness.next_nonce + luck.grant_raffle_tickets.

Revision ID: 0024_luck_helpers
Revises: 0023_luck_resolve_refund_cashout
Create Date: 2026-05-04

Closes Story 2.8c. Three small helper SDFs that the bot's cogs
will call alongside ``apply_bet`` / ``resolve_bet``:

## luck.consume_rate_token(p_discord_id, p_scope, p_window_s, p_max_count)
RETURNS BOOLEAN

Atomic increment-and-check of a per-(user, scope) sliding bucket.
Returns TRUE if the action is allowed, FALSE if the user has hit
the cap inside the current window.

The bucket alignment is epoch-based: ``bucket_start = floor(now /
window_s) * window_s``. Each fixed window is its own row. The
existing ``UNIQUE (discord_id, scope, bucket_start)`` constraint
on ``luck.rate_limit_entries`` (Story 2.7) is the atomicity anchor:
the SDF uses ``INSERT ... ON CONFLICT DO UPDATE SET count =
count + 1 RETURNING count`` so ten parallel callers all serialise
through Postgres' row-level locking.

Old buckets are NOT cleaned up by this fn; a separate hourly
worker (deferred to a later epic) will purge stale rows.

## fairness.next_nonce(p_discord_id) RETURNS BIGINT

Atomically increments ``fairness.user_seeds.nonce`` and returns the
PRE-increment value (i.e., the nonce the caller should USE).

Used by every game's outcome derivation as the third input to
HMAC-SHA512: ``out = HMAC-SHA512(server_seed, client_seed || ":"
|| nonce)``. The fn raises ``seed_not_found`` if the user has no
user_seeds row (game flows must call ``fairness.rotate_user_seed``
in Story 2.8d to lazily provision a row on first play).

## luck.grant_raffle_tickets(p_discord_id, p_period_id,
                             p_ticket_count, p_bet_id) RETURNS INT

Bulk insert of ``p_ticket_count`` rows in ``luck.raffle_tickets``
linked to (period, user, bet). Returns the number inserted. Used
by the bet-settlement flow when a user crosses a
``raffle_ticket_threshold_g`` (Story 2.9 seed: 100 G) of wagered
volume — they earn one ticket per threshold step.

Error paths:
- ``invalid_ticket_count``  — p_ticket_count < 0
- ``period_not_found``      — p_period_id has no row
- ``period_not_active``     — period.status != 'active'
- ``zero count``            — early-return with 0 (no rows inserted)

## Privilege

REVOKE ALL FROM PUBLIC; GRANT EXECUTE TO deathroll_luck only on
all three. ``deathroll_readonly`` cannot call any of them.
"""

from alembic import op

revision = "0024_luck_helpers"
down_revision = "0023_luck_resolve_refund_cashout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # luck.consume_rate_token
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.consume_rate_token(
        p_discord_id BIGINT,
        p_scope      TEXT,
        p_window_s   INT,
        p_max_count  INT
    )
    RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, pg_catalog AS $$
    DECLARE
        v_bucket_start TIMESTAMPTZ;
        v_count        INT;
    BEGIN
        IF p_window_s <= 0 OR p_max_count <= 0 THEN
            RAISE EXCEPTION 'invalid_rate_limit_args: window_s=% max_count=%',
                p_window_s, p_max_count;
        END IF;

        -- Align bucket to epoch-based window boundary so concurrent
        -- callers within the same window all hit the same row.
        v_bucket_start := to_timestamp(
            (EXTRACT(EPOCH FROM NOW())::BIGINT / p_window_s) * p_window_s
        );

        -- Atomic increment-or-insert. The UNIQUE constraint on
        -- (discord_id, scope, bucket_start) is the lock anchor.
        INSERT INTO luck.rate_limit_entries
            (discord_id, scope, bucket_start, count)
        VALUES (p_discord_id, p_scope, v_bucket_start, 1)
        ON CONFLICT (discord_id, scope, bucket_start) DO UPDATE
            SET count = luck.rate_limit_entries.count + 1
        RETURNING count INTO v_count;

        RETURN v_count <= p_max_count;
    END;
    $$;

    REVOKE ALL ON FUNCTION luck.consume_rate_token(BIGINT, TEXT, INT, INT)
        FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION luck.consume_rate_token(BIGINT, TEXT, INT, INT)
        TO deathroll_luck;
    """)

    # -----------------------------------------------------------------
    # fairness.next_nonce
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION fairness.next_nonce(
        p_discord_id BIGINT
    )
    RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = fairness, pg_catalog AS $$
    DECLARE
        v_used BIGINT;
    BEGIN
        UPDATE fairness.user_seeds
        SET nonce = nonce + 1
        WHERE discord_id = p_discord_id
        RETURNING nonce - 1 INTO v_used;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'seed_not_found: %', p_discord_id;
        END IF;

        RETURN v_used;
    END;
    $$;

    REVOKE ALL ON FUNCTION fairness.next_nonce(BIGINT) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION fairness.next_nonce(BIGINT) TO deathroll_luck;
    """)

    # -----------------------------------------------------------------
    # luck.grant_raffle_tickets
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.grant_raffle_tickets(
        p_discord_id    BIGINT,
        p_period_id     BIGINT,
        p_ticket_count  INT,
        p_bet_id        BIGINT
    )
    RETURNS INT
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, pg_catalog AS $$
    DECLARE
        v_period_status TEXT;
    BEGIN
        IF p_ticket_count < 0 THEN
            RAISE EXCEPTION 'invalid_ticket_count: %', p_ticket_count;
        END IF;
        IF p_ticket_count = 0 THEN
            RETURN 0;
        END IF;

        SELECT status INTO v_period_status
        FROM luck.raffle_periods WHERE id = p_period_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'period_not_found: %', p_period_id;
        END IF;
        IF v_period_status <> 'active' THEN
            RAISE EXCEPTION 'period_not_active: id=% status=%',
                p_period_id, v_period_status;
        END IF;

        INSERT INTO luck.raffle_tickets (period_id, discord_id, bet_id)
        SELECT p_period_id, p_discord_id, p_bet_id
        FROM generate_series(1, p_ticket_count);

        RETURN p_ticket_count;
    END;
    $$;

    REVOKE ALL ON FUNCTION luck.grant_raffle_tickets(BIGINT, BIGINT, INT, BIGINT)
        FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION luck.grant_raffle_tickets(BIGINT, BIGINT, INT, BIGINT)
        TO deathroll_luck;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS luck.grant_raffle_tickets(BIGINT, BIGINT, INT, BIGINT);
        DROP FUNCTION IF EXISTS fairness.next_nonce(BIGINT);
        DROP FUNCTION IF EXISTS luck.consume_rate_token(BIGINT, TEXT, INT, INT);
    """)
