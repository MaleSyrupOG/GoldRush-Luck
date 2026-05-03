# ADR 0015 — Treasury is a `core.balances` row at `discord_id = 0`

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

The bot collects withdraw fees (default 2 % of the withdraw amount) into a "treasury". This treasury accumulates between admin sweeps; admins periodically:

- Sweep treasury down to zero via `/admin-treasury-sweep` (records the swept amount as transferred to "outside the bot" — i.e., to the operator's in-game guild bank).
- Withdraw treasury directly to a user via `/admin-treasury-withdraw-to-user` (refunds, dispute resolutions, jackpot prizes if any).

The treasury must:

1. Be a number that goes up and down conservatively (every increment ties to a confirmed withdraw fee; every decrement ties to an admin action).
2. Be auditable end-to-end through the same audit log as user balances.
3. Be invariant-checkable: `SUM(user balances) + treasury_balance + admin_swept_total == total_ever_deposited`.
4. Have a representation that does NOT need its own table or schema.

## Decision

**The treasury is a single row in `core.balances` at `discord_id = 0`.** Not a separate table. Not a separate schema. The same SECURITY DEFINER fns that move user balance also move treasury balance, with `discord_id = 0` as the source or destination.

Concretely:

- `core.balances` schema: `(discord_id BIGINT PRIMARY KEY, balance_g BIGINT NOT NULL, ...)`. The row at `discord_id = 0` is seeded by migration `0001_core_users_balances.py` at install time.
- `dw.confirm_withdraw` debits the user (`UPDATE core.balances SET balance_g = balance_g - amount WHERE discord_id = $1`) AND credits the treasury (`UPDATE core.balances SET balance_g = balance_g + fee WHERE discord_id = 0`) in the same transaction. Both rows are `FOR UPDATE`-locked.
- `dw.treasury_sweep` debits `discord_id = 0` and emits a `treasury_swept` audit row with the swept amount.
- `dw.treasury_withdraw_to_user` debits `discord_id = 0` and credits the target user, emitting both halves into the audit log (the receiving user sees a `transfer_in` audit; the treasury sees a `treasury_withdraw_to_user` audit).
- `core.users` row at `discord_id = 0` carries `username = '__treasury__'` so any admin tool that joins users to balances renders the treasury legibly. It is the only `core.users` row created without a corresponding Discord user.

## Reasons we chose a `discord_id = 0` row over a dedicated table

1. **One ledger, one invariant.** Every gold-G in the system lives in `core.balances`. The treasury invariant is then `SUM(core.balances.balance_g) == total_ever_deposited - total_ever_swept`, computable in a single query. A separate `core.treasury` table would make the invariant a join across two tables.

2. **Same audit-log machinery.** Every change to `core.balances` already emits to `core.audit_log` via the trigger-driven hash chain. A separate treasury table would need its own emitter, triggers, and chain integration. Folding into `core.balances` reuses the entire audit infrastructure for free.

3. **Same SECURITY DEFINER boundary.** Treasury operations route through the same `dw.*` SDFs that handle user balances. There's only one schema with the privilege to touch `core.balances`. Reduces the SDF count by ~3 fns.

4. **`discord_id = 0` is a safe sentinel.** Discord snowflake ids are 64-bit and start at the Discord epoch (2015-01-01). The id `0` cannot be assigned to any real Discord user. Using it as the system-account anchor is a 100-year-safe choice.

5. **One row, one lock.** A treasury sweep concurrent with a withdraw confirm both lock `discord_id = 0` row. PostgreSQL serialises them via row-level locking. There is no separate lock surface to reason about.

## Consequences

Positive:

- The treasury invariant is a single SQL query: `SELECT SUM(balance_g) FROM core.balances`. Pinned in the property test `test_treasury_invariant_holds_under_concurrency`.
- The audit log naturally contains every gold movement, including treasury-to-user flows. Querying "where did this gold go" never has to consult two ledgers.
- A "treasury balance" view is `SELECT balance_g FROM core.balances WHERE discord_id = 0`. Same code path as user balance lookups.

Negative:

- A naive `SELECT * FROM core.balances` returns one extra row for the treasury. Admin queries that report user balances must `WHERE discord_id != 0` (a one-line filter). Helpers in `deathroll_core/balance/queries.py` add this automatically; pinned by the unit test `test_user_balance_listing_excludes_treasury`.
- The `core.users` row at `discord_id = 0` is a synthetic user. Any future "list all users" surface must skip it. Only one place in the code does this today (the `users` admin export); flagged with an inline comment.

## Alternatives considered

- **Dedicated `core.treasury` table with its own audit-log column**: rejected because of the duplication of audit infrastructure and the invariant-becomes-join cost.
- **Treasury balance held in `dw.global_config` as a JSONB blob**: rejected because gold movement loses transactional atomicity with user balance changes.
- **Treasury accumulated in a second column on every `core.balances` row** (`fee_balance_g`): rejected as semantically confused — fee dollars don't belong to individual users.

## References

- D/W design spec §3.1 (treasury model)
- D/W design spec §6.4 (anti-fraud — treasury draining)
- Migration `0011_dw_treasury.py` (the SDFs `treasury_sweep`, `treasury_withdraw_to_user`)
- Property test `tests/integration/dw/test_treasury_invariant.py`
- `deathroll_core/balance/queries.py::list_user_balances` (the `discord_id != 0` filter)
