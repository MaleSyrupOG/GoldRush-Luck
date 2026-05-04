"""luck.bets resolution columns + resolve_bet, refund_bet, cashout_mines.

Revision ID: 0023_luck_resolve_refund_cashout
Revises: 0022_luck_apply_bet
Create Date: 2026-05-04

Closes Story 2.8b. Adds the bet-resolution machinery — the SDFs
that close out an ``open`` bet, plus the four columns on
``luck.bets`` they need to do so cleanly.

## Schema additions on luck.bets

Four columns are added to make resolution self-contained — without
them, ``resolve_bet`` would have to recompute the apply-time
commission/rake/effective_stake from current ``game_config`` +
``global_config``, which is wrong when an admin tunes config
between apply and resolve.

- ``effective_stake`` BIGINT — the playable portion (= bet_amount
  - commission - rake). Held in user's ``locked_balance`` until
  resolve. Resolve releases exactly this amount from the lock.
- ``commission`` BIGINT — the upfront house cut (zero for
  parametric games; 4.5% for blackjack; 2.36% for roulette per
  Story 2.9 seeds). Captured to treasury at apply time.
- ``rake`` BIGINT — the raffle rake (1% per Story 2.9 seeds).
  Captured to either the active raffle period's pool OR
  treasury (if no active period at apply time).
- ``rake_period_id`` BIGINT REFERENCES luck.raffle_periods(id) —
  which period the rake went to. NULL means rake fell to treasury.

The new columns default to 0 / NULL. Existing rows get those
defaults; in production there are zero existing rows because
Luck hasn't shipped yet.

## CREATE OR REPLACE luck.apply_bet

Recreates ``luck.apply_bet`` with two changes:
- Persists ``(effective_stake, commission, rake, rake_period_id)``
  into ``luck.bets`` at INSERT time.
- Same signature, same return, same gold-flow semantics.

## New SDFs

### luck.resolve_bet(p_bet_id, p_status, p_payout, p_outcome)

Closes an ``open`` bet with a terminal status:

- ``resolved_win``  — balance += payout; locked -= effective_stake;
                     treasury -= (payout - effective_stake);
                     total_won += payout.
- ``resolved_loss`` — balance += 0; locked -= effective_stake;
                     treasury += effective_stake. Lost stake goes
                     to the house.
- ``resolved_tie``  — balance += effective_stake; locked -=
                     effective_stake; treasury delta = 0. The
                     house keeps commission + rake even on a tie
                     (v1 design decision; documented in
                     ``treasury-management.md`` and the spec).

Idempotent on a re-call with the same status (no-op). Switching
status mid-way (loss → win) raises ``bet_already_terminal``.

### luck.refund_bet(p_bet_id, p_reason)

Full unwind of apply_bet. Used for void/error/admin-cancel paths:

- balance += bet_amount (full refund)
- locked -= effective_stake
- treasury -= commission
- pool of rake_period_id -= rake (or treasury -= rake if rake fell
  to treasury at apply time)
- total_wagered -= bet_amount (the wager never happened)
- bet status -> ``refunded``

Idempotent. Cannot refund a non-open bet (raises
``bet_already_terminal``).

### luck.cashout_mines(p_bet_id, p_multiplier)

Mid-game cashout for Mines. Computes payout = effective_stake *
multiplier and dispatches to ``resolve_bet`` with status =
``resolved_win``. Validates that the bet's game_name = 'mines'
and the multiplier > 0.

## Audit

Every transition writes an audit row via the chain helper:
- bet_resolved (resolve)
- bet_refunded (refund)
- (cashout_mines emits via its resolve_bet inner call)

## Privilege

REVOKE ALL FROM PUBLIC; GRANT EXECUTE TO deathroll_luck only.
deathroll_dw and deathroll_readonly cannot call any of the three
fns (verified by ``test_*_readonly_no_execute``).
"""

from alembic import op

revision = "0023_luck_resolve_refund_cashout"
down_revision = "0022_luck_apply_bet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. ALTER TABLE luck.bets — add the four resolution columns.
    # -----------------------------------------------------------------
    op.execute("""
        ALTER TABLE luck.bets
            ADD COLUMN effective_stake BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN commission      BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN rake            BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN rake_period_id  BIGINT REFERENCES luck.raffle_periods(id)
                                              ON DELETE RESTRICT;
    """)

    # -----------------------------------------------------------------
    # 2. CREATE OR REPLACE luck.apply_bet — populate the new columns.
    # -----------------------------------------------------------------
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

        SELECT
            enabled, min_bet, max_bet,
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

        SELECT COALESCE(value_int, 0) INTO v_rake_bps
        FROM luck.global_config WHERE key = 'raffle_rake_bps';
        IF v_rake_bps IS NULL THEN
            v_rake_bps := 0;
        END IF;

        v_commission      := (p_bet_amount * v_commission_bps) / 10000;
        v_rake            := (p_bet_amount * v_rake_bps) / 10000;
        v_effective_stake := p_bet_amount - v_commission - v_rake;

        SELECT balance INTO v_user_balance
        FROM core.balances
        WHERE discord_id = p_discord_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'user_not_registered: %', p_discord_id;
        END IF;

        SELECT banned INTO v_user_banned
        FROM core.users WHERE discord_id = p_discord_id;
        IF v_user_banned THEN
            RAISE EXCEPTION 'user_banned: %', p_discord_id;
        END IF;

        IF v_user_balance < p_bet_amount THEN
            RAISE EXCEPTION 'insufficient_balance: have=% need=%',
                v_user_balance, p_bet_amount;
        END IF;

        v_balance_before := v_user_balance;
        v_balance_after  := v_user_balance - p_bet_amount;

        SELECT id INTO v_active_period
        FROM luck.raffle_periods
        WHERE status = 'active'
        ORDER BY id DESC LIMIT 1;

        UPDATE core.balances SET
            balance        = balance - p_bet_amount,
            locked_balance = locked_balance + v_effective_stake,
            total_wagered  = total_wagered + p_bet_amount,
            updated_at     = NOW(),
            version        = version + 1
        WHERE discord_id = p_discord_id;

        IF v_commission > 0 THEN
            UPDATE core.balances SET
                balance    = balance + v_commission,
                updated_at = NOW(),
                version    = version + 1
            WHERE discord_id = 0;
        END IF;

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

        -- Now persists the four resolution columns alongside the bet.
        INSERT INTO luck.bets (
            bet_uid, discord_id, game_name, channel_id, bet_amount,
            selection, status, server_seed_hash, client_seed, nonce,
            idempotency_key,
            effective_stake, commission, rake, rake_period_id
        ) VALUES (
            p_bet_uid, p_discord_id, p_game_name, p_channel_id,
            p_bet_amount, p_selection, 'open', p_server_seed_hash,
            p_client_seed, p_nonce, p_idempotency_key,
            v_effective_stake, v_commission, v_rake, v_active_period
        )
        RETURNING id INTO v_new_bet_id;

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
    """)

    # -----------------------------------------------------------------
    # 3. luck.resolve_bet
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.resolve_bet(
        p_bet_id   BIGINT,
        p_status   TEXT,
        p_payout   BIGINT,
        p_outcome  JSONB
    )
    RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, core, pg_catalog AS $$
    DECLARE
        v_bet              luck.bets%ROWTYPE;
        v_user_balance     BIGINT;
        v_balance_before   BIGINT;
        v_balance_after    BIGINT;
        v_treasury_delta   BIGINT;
        v_credit_user      BIGINT;
    BEGIN
        IF p_status NOT IN ('resolved_win','resolved_loss','resolved_tie') THEN
            RAISE EXCEPTION 'invalid_status: %', p_status;
        END IF;
        IF p_payout < 0 THEN
            RAISE EXCEPTION 'invalid_payout: %', p_payout;
        END IF;

        SELECT * INTO v_bet
        FROM luck.bets
        WHERE id = p_bet_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'bet_not_found: %', p_bet_id;
        END IF;

        -- Idempotency: same terminal status with same payout = no-op.
        IF v_bet.status = p_status AND v_bet.payout_amount = p_payout THEN
            RETURN;
        END IF;
        IF v_bet.status <> 'open' THEN
            RAISE EXCEPTION
                'bet_already_terminal: id=% current=% requested=%',
                p_bet_id, v_bet.status, p_status;
        END IF;

        -- Lock the user's balance row before any state mutation.
        SELECT balance INTO v_user_balance
        FROM core.balances
        WHERE discord_id = v_bet.discord_id
        FOR UPDATE;
        v_balance_before := v_user_balance;

        -- Compute how much credits the user, and the treasury delta.
        IF p_status = 'resolved_win' THEN
            v_credit_user := p_payout;
            v_treasury_delta := v_bet.effective_stake - p_payout;
        ELSIF p_status = 'resolved_loss' THEN
            v_credit_user := 0;
            v_treasury_delta := v_bet.effective_stake;
        ELSE  -- resolved_tie
            v_credit_user := v_bet.effective_stake;
            v_treasury_delta := 0;
        END IF;

        -- Apply user-side updates.
        UPDATE core.balances SET
            balance        = balance + v_credit_user,
            locked_balance = locked_balance - v_bet.effective_stake,
            total_won      = total_won + v_credit_user,
            updated_at     = NOW(),
            version        = version + 1
        WHERE discord_id = v_bet.discord_id;
        v_balance_after := v_balance_before + v_credit_user;

        -- Treasury delta (positive = treasury gains, negative = treasury pays).
        IF v_treasury_delta <> 0 THEN
            UPDATE core.balances SET
                balance    = balance + v_treasury_delta,
                updated_at = NOW(),
                version    = version + 1
            WHERE discord_id = 0;
        END IF;

        -- Persist resolution on the bet row.
        UPDATE luck.bets SET
            status        = p_status,
            payout_amount = p_payout,
            profit        = p_payout - bet_amount,
            outcome       = p_outcome,
            resolved_at   = NOW()
        WHERE id = p_bet_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'system',
            p_actor_id       := v_bet.discord_id,
            p_target_id      := v_bet.discord_id,
            p_action         := 'bet_resolved',
            p_amount         := p_payout,
            p_balance_before := v_balance_before,
            p_balance_after  := v_balance_after,
            p_reason         := format(
                'Bet resolved: %s status=%s payout=%s',
                v_bet.bet_uid, p_status, p_payout
            ),
            p_ref_type       := 'luck_bet',
            p_ref_id         := p_bet_id::TEXT,
            p_bot_name       := 'luck',
            p_metadata       := jsonb_build_object(
                'game_name',       v_bet.game_name,
                'bet_uid',         v_bet.bet_uid,
                'status',          p_status,
                'effective_stake', v_bet.effective_stake,
                'treasury_delta',  v_treasury_delta,
                'outcome',         p_outcome
            )
        );
    END;
    $$;
    """)

    # -----------------------------------------------------------------
    # 4. luck.refund_bet
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.refund_bet(
        p_bet_id BIGINT,
        p_reason TEXT
    )
    RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, core, pg_catalog AS $$
    DECLARE
        v_bet              luck.bets%ROWTYPE;
        v_balance_before   BIGINT;
        v_balance_after    BIGINT;
    BEGIN
        SELECT * INTO v_bet
        FROM luck.bets
        WHERE id = p_bet_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'bet_not_found: %', p_bet_id;
        END IF;

        -- Idempotency: re-running on a refunded bet is a no-op.
        IF v_bet.status = 'refunded' THEN
            RETURN;
        END IF;
        IF v_bet.status <> 'open' THEN
            RAISE EXCEPTION
                'bet_already_terminal: id=% current=%',
                p_bet_id, v_bet.status;
        END IF;

        -- Lock the user's balance row.
        SELECT balance INTO v_balance_before
        FROM core.balances
        WHERE discord_id = v_bet.discord_id
        FOR UPDATE;

        -- Full unwind: user gets bet_amount back, locked released,
        -- total_wagered reverts, treasury gives back commission, pool
        -- gives back rake (or treasury if rake fell to treasury at
        -- apply time).
        UPDATE core.balances SET
            balance        = balance + v_bet.bet_amount,
            locked_balance = locked_balance - v_bet.effective_stake,
            total_wagered  = total_wagered - v_bet.bet_amount,
            updated_at     = NOW(),
            version        = version + 1
        WHERE discord_id = v_bet.discord_id;
        v_balance_after := v_balance_before + v_bet.bet_amount;

        IF v_bet.commission > 0 THEN
            UPDATE core.balances SET
                balance    = balance - v_bet.commission,
                updated_at = NOW(),
                version    = version + 1
            WHERE discord_id = 0;
        END IF;

        IF v_bet.rake > 0 THEN
            IF v_bet.rake_period_id IS NOT NULL THEN
                UPDATE luck.raffle_periods
                SET pool_amount = pool_amount - v_bet.rake
                WHERE id = v_bet.rake_period_id;
            ELSE
                UPDATE core.balances SET
                    balance    = balance - v_bet.rake,
                    updated_at = NOW(),
                    version    = version + 1
                WHERE discord_id = 0;
            END IF;
        END IF;

        UPDATE luck.bets SET
            status        = 'refunded',
            payout_amount = 0,
            profit        = 0,
            outcome       = jsonb_build_object('reason', p_reason),
            resolved_at   = NOW()
        WHERE id = p_bet_id;

        PERFORM core.audit_log_insert_with_chain(
            p_actor_type     := 'system',
            p_actor_id       := v_bet.discord_id,
            p_target_id      := v_bet.discord_id,
            p_action         := 'bet_refunded',
            p_amount         := v_bet.bet_amount,
            p_balance_before := v_balance_before,
            p_balance_after  := v_balance_after,
            p_reason         := format(
                'Bet refunded: %s reason=%s', v_bet.bet_uid, p_reason
            ),
            p_ref_type       := 'luck_bet',
            p_ref_id         := p_bet_id::TEXT,
            p_bot_name       := 'luck',
            p_metadata       := jsonb_build_object(
                'game_name', v_bet.game_name,
                'bet_uid',   v_bet.bet_uid,
                'reason',    p_reason
            )
        );
    END;
    $$;
    """)

    # -----------------------------------------------------------------
    # 5. luck.cashout_mines
    # -----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION luck.cashout_mines(
        p_bet_id     BIGINT,
        p_multiplier NUMERIC
    )
    RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = luck, core, pg_catalog AS $$
    DECLARE
        v_bet      luck.bets%ROWTYPE;
        v_payout   BIGINT;
    BEGIN
        IF p_multiplier <= 0 THEN
            RAISE EXCEPTION 'invalid_multiplier: %', p_multiplier;
        END IF;

        SELECT * INTO v_bet
        FROM luck.bets WHERE id = p_bet_id FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'bet_not_found: %', p_bet_id;
        END IF;
        IF v_bet.game_name <> 'mines' THEN
            RAISE EXCEPTION 'not_mines_bet: %', v_bet.game_name;
        END IF;
        IF v_bet.status <> 'open' THEN
            RAISE EXCEPTION
                'bet_already_terminal: id=% current=%',
                p_bet_id, v_bet.status;
        END IF;

        -- Compute payout via the bet's effective_stake. FLOOR to
        -- avoid fractional gold.
        v_payout := FLOOR(v_bet.effective_stake * p_multiplier)::BIGINT;

        -- Dispatch to resolve_bet (which writes the audit row +
        -- updates balances + treasury). Note: the inner call shares
        -- the same transaction so the FOR UPDATE locks taken there
        -- are non-conflicting here (we already hold them).
        PERFORM luck.resolve_bet(
            p_bet_id  := p_bet_id,
            p_status  := 'resolved_win',
            p_payout  := v_payout,
            p_outcome := jsonb_build_object(
                'cashout', TRUE,
                'multiplier', p_multiplier::TEXT
            )
        );
    END;
    $$;
    """)

    # -----------------------------------------------------------------
    # 6. Privilege grants for all three new fns + the recreated apply_bet.
    # -----------------------------------------------------------------
    op.execute("""
        REVOKE ALL ON FUNCTION luck.resolve_bet(BIGINT, TEXT, BIGINT, JSONB)
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION luck.resolve_bet(BIGINT, TEXT, BIGINT, JSONB)
            TO deathroll_luck;

        REVOKE ALL ON FUNCTION luck.refund_bet(BIGINT, TEXT) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION luck.refund_bet(BIGINT, TEXT)
            TO deathroll_luck;

        REVOKE ALL ON FUNCTION luck.cashout_mines(BIGINT, NUMERIC) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION luck.cashout_mines(BIGINT, NUMERIC)
            TO deathroll_luck;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS luck.cashout_mines(BIGINT, NUMERIC);
        DROP FUNCTION IF EXISTS luck.refund_bet(BIGINT, TEXT);
        DROP FUNCTION IF EXISTS luck.resolve_bet(BIGINT, TEXT, BIGINT, JSONB);
    """)
    # Restore apply_bet to its 0022 form (the variant that did NOT
    # populate the four resolution columns). For simplicity we just
    # drop it — re-applying 0022's upgrade restores it.
    op.execute("""
        DROP FUNCTION IF EXISTS luck.apply_bet(
            BIGINT, TEXT, BIGINT, BIGINT, JSONB, BYTEA, TEXT, BIGINT, TEXT, TEXT
        );
    """)
    op.execute("""
        ALTER TABLE luck.bets
            DROP COLUMN IF EXISTS rake_period_id,
            DROP COLUMN IF EXISTS rake,
            DROP COLUMN IF EXISTS commission,
            DROP COLUMN IF EXISTS effective_stake;
    """)
