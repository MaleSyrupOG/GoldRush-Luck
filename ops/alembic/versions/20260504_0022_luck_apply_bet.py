"""luck.apply_bet — the SECURITY DEFINER fn that opens a bet.

Revision ID: 0022_luck_apply_bet
Revises: 0021_luck_seed_defaults
Create Date: 2026-05-04

Implements Luck design §3.4 (stored procedures — economic boundary).

``luck.apply_bet`` is the entry point for every game: it locks the
user's balance row, validates funds + game state, captures the
house commission and raffle rake, debits the user's balance, and
inserts the ``luck.bets`` row in state ``open``. From this point
on the bet is "in flight" — the corresponding ``luck.resolve_bet``
or ``luck.refund_bet`` (Story 2.8b) closes it out.

Idempotency: the fn is idempotent on
``(p_discord_id, p_idempotency_key)``. A second call with the
same pair returns the existing ``(bet_id, idempotent=TRUE)``
without any side effects. This protects against double-charges
when the bot retries an interaction or recovers from a partial
crash.

Conservation invariant (verified by ``test_apply_bet_conservation``):

    delta(user.balance + user.locked_balance + treasury + raffle_pool) == 0

Gold flow at apply_bet:

  user.balance         -= bet_amount         (full bet leaves balance)
  user.locked_balance  += effective_stake    (the playable portion;
                                              same row, just memo'd)
  user.total_wagered   += bet_amount         (lifetime stat)
  treasury.balance     += commission         (house edge upfront for
                                              rule-based games; 0 for
                                              parametric games)
  active_period.pool   += rake               (raffle pool gains the
                                              raffle_rake_bps cut)

If no raffle period is active (status='active' missing), the
rake is captured to treasury instead of dropping on the floor —
that way gold is never lost during a temporarily-paused raffle.

Errors raised (all named so cogs can map them to Pydantic types):

  - ``user_not_registered``    — no core.balances row for the user
  - ``user_banned``            — core.users.banned = TRUE
  - ``unknown_game``           — game_name not in luck.game_config
  - ``game_paused``            — game_config.enabled = FALSE
  - ``bet_out_of_range``       — bet_amount outside [min_bet, max_bet]
  - ``insufficient_balance``   — user.balance < bet_amount
"""

from alembic import op

revision = "0022_luck_apply_bet"
down_revision = "0021_luck_seed_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.apply_bet(
        p_discord_id       BIGINT,
        p_game_name        TEXT,
        p_channel_id       BIGINT,
        p_bet_amount       BIGINT,
        p_selection        JSONB,
        p_server_seed_hash BYTEA,
        p_client_seed      TEXT,
        p_nonce            BIGINT,
        p_idempotency_key  TEXT,
        p_bet_uid          TEXT
    )
    RETURNS TABLE (bet_id BIGINT, idempotent BOOLEAN)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, core, pg_catalog AS $$
    DECLARE
        v_existing_id      BIGINT;
        v_game_enabled     BOOLEAN;
        v_min_bet          BIGINT;
        v_max_bet          BIGINT;
        v_commission_bps   INT;
        v_rake_bps         INT;
        v_commission       BIGINT;
        v_rake             BIGINT;
        v_effective_stake  BIGINT;
        v_user_balance     BIGINT;
        v_balance_before   BIGINT;
        v_balance_after    BIGINT;
        v_user_banned      BOOLEAN;
        v_active_period    BIGINT;
        v_new_bet_id       BIGINT;
    BEGIN
        -- 1. Idempotency — same key for same user returns the existing
        --    bet without re-running side effects.
        SELECT id INTO v_existing_id
        FROM luck.bets
        WHERE discord_id = p_discord_id
          AND idempotency_key = p_idempotency_key;

        IF v_existing_id IS NOT NULL THEN
            bet_id := v_existing_id;
            idempotent := TRUE;
            RETURN NEXT;
            RETURN;
        END IF;

        -- 2. Load game config + validate.
        SELECT
            enabled,
            min_bet,
            max_bet,
            COALESCE((extra_config->>'commission_bps')::INT, 0)
        INTO v_game_enabled, v_min_bet, v_max_bet, v_commission_bps
        FROM luck.game_config
        WHERE game_name = p_game_name;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'unknown_game: %', p_game_name;
        END IF;
        IF NOT v_game_enabled THEN
            RAISE EXCEPTION 'game_paused: %', p_game_name;
        END IF;
        IF p_bet_amount < v_min_bet OR p_bet_amount > v_max_bet THEN
            RAISE EXCEPTION
                'bet_out_of_range: amount=% min=% max=%',
                p_bet_amount, v_min_bet, v_max_bet;
        END IF;

        -- 3. Load raffle rake bps from global_config (default 0 if missing).
        SELECT COALESCE(value_int, 0) INTO v_rake_bps
        FROM luck.global_config
        WHERE key = 'raffle_rake_bps';
        IF v_rake_bps IS NULL THEN
            v_rake_bps := 0;
        END IF;

        -- 4. Compute commission, rake, effective stake.
        v_commission      := (p_bet_amount * v_commission_bps) / 10000;
        v_rake            := (p_bet_amount * v_rake_bps) / 10000;
        v_effective_stake := p_bet_amount - v_commission - v_rake;

        -- 5. Lock user's balance row FOR UPDATE; verify existence.
        SELECT balance INTO v_user_balance
        FROM core.balances
        WHERE discord_id = p_discord_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'user_not_registered: %', p_discord_id;
        END IF;

        -- 6. Banned check.
        SELECT banned INTO v_user_banned
        FROM core.users
        WHERE discord_id = p_discord_id;
        IF v_user_banned THEN
            RAISE EXCEPTION 'user_banned: %', p_discord_id;
        END IF;

        -- 7. Sufficient funds.
        IF v_user_balance < p_bet_amount THEN
            RAISE EXCEPTION
                'insufficient_balance: have=% need=%',
                v_user_balance, p_bet_amount;
        END IF;

        v_balance_before := v_user_balance;
        v_balance_after  := v_user_balance - p_bet_amount;

        -- 8. Find the active raffle period (if any).
        SELECT id INTO v_active_period
        FROM luck.raffle_periods
        WHERE status = 'active'
        ORDER BY id DESC
        LIMIT 1;

        -- 9. Apply the gold movements.
        UPDATE core.balances SET
            balance        = balance - p_bet_amount,
            locked_balance = locked_balance + v_effective_stake,
            total_wagered  = total_wagered + p_bet_amount,
            updated_at     = NOW(),
            version        = version + 1
        WHERE discord_id = p_discord_id;

        -- Treasury gets the commission. The treasury row at
        -- discord_id=0 is seeded by migration 0001.
        IF v_commission > 0 THEN
            UPDATE core.balances SET
                balance    = balance + v_commission,
                updated_at = NOW(),
                version    = version + 1
            WHERE discord_id = 0;
        END IF;

        -- Raffle pool gets the rake; if no active period, the rake
        -- falls to treasury so gold is never lost.
        IF v_rake > 0 THEN
            IF v_active_period IS NOT NULL THEN
                UPDATE luck.raffle_periods
                SET pool_amount = pool_amount + v_rake
                WHERE id = v_active_period;
            ELSE
                UPDATE core.balances SET
                    balance    = balance + v_rake,
                    updated_at = NOW(),
                    version    = version + 1
                WHERE discord_id = 0;
            END IF;
        END IF;

        -- 10. Insert the bet row.
        INSERT INTO luck.bets (
            bet_uid, discord_id, game_name, channel_id, bet_amount,
            selection, status, server_seed_hash, client_seed, nonce,
            idempotency_key
        ) VALUES (
            p_bet_uid, p_discord_id, p_game_name, p_channel_id,
            p_bet_amount, p_selection, 'open', p_server_seed_hash,
            p_client_seed, p_nonce, p_idempotency_key
        )
        RETURNING id INTO v_new_bet_id;

        -- 11. Audit row via the chain helper.
        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'user',
            p_actor_id       := p_discord_id,
            p_target_id      := p_discord_id,
            p_action         := 'bet_placed',
            p_amount         := p_bet_amount,
            p_balance_before := v_balance_before,
            p_balance_after  := v_balance_after,
            p_reason         := format(
                'Bet placed: %s %s G on %s', p_bet_uid, p_bet_amount, p_game_name
            ),
            p_ref_type       := 'luck_bet',
            p_ref_id         := v_new_bet_id::TEXT,
            p_bot_name       := 'luck',
            p_metadata       := jsonb_build_object(
                'game_name',       p_game_name,
                'bet_uid',         p_bet_uid,
                'channel_id',      p_channel_id,
                'commission',      v_commission,
                'rake',            v_rake,
                'effective_stake', v_effective_stake,
                'nonce',           p_nonce
            )
        );

        bet_id := v_new_bet_id;
        idempotent := FALSE;
        RETURN NEXT;
        RETURN;
    END;
    $$;

    REVOKE ALL ON FUNCTION luck.apply_bet(
        BIGINT, TEXT, BIGINT, BIGINT, JSONB, BYTEA, TEXT, BIGINT, TEXT, TEXT
    ) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION luck.apply_bet(
        BIGINT, TEXT, BIGINT, BIGINT, JSONB, BYTEA, TEXT, BIGINT, TEXT, TEXT
    ) TO deathroll_luck;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS luck.apply_bet(
            BIGINT, TEXT, BIGINT, BIGINT, JSONB, BYTEA, TEXT, BIGINT, TEXT, TEXT
        );
    """)
