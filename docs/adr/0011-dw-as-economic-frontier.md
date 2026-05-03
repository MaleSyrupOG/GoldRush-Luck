# ADR 0011 — Deposit/Withdraw is the only economic frontier

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

DeathRoll has three bots that share a single PostgreSQL database — Luck (games), Deposit/Withdraw (banking), Poker (future). Each bot has its own role in the database (`deathroll_luck`, `deathroll_dw`, `deathroll_poker`) and its own slash-command surface.

Every transaction the platform mediates ultimately moves WoW Gold between two states:

- **Outside-the-bot reality** — actual gold in a player's character or in an in-game guild bank.
- **Inside-the-bot ledger** — a row in `core.balances` keyed by Discord user id.

Crossing that boundary is a uniquely sensitive operation: a wrong row in `core.balances` mints or destroys gold from the user's perspective. By contrast, in-game game outcomes (a Coinflip win, a Dice loss) only redistribute existing balance between users; nothing is minted or destroyed.

## Decision

**Only the Deposit/Withdraw bot is allowed to mint or destroy `core.balances` rows.** Every other bot — Luck and (future) Poker — can read user balances and can move balance between users via the SECURITY DEFINER game-settlement functions (`luck.settle_bet_*` etc.), but they cannot create new value out of thin air or remove value to nothing.

This is enforced at three layers:

1. **Postgres role grants** — `deathroll_luck` has `SELECT, UPDATE` on `core.balances` only via the SECURITY DEFINER game-settlement functions; it does NOT have direct GRANTs on `core.balances`. `deathroll_dw` is the only role with EXECUTE on `dw.confirm_deposit` and `dw.confirm_withdraw`, the two functions that change `core.balances` outside of game settlement. Verified by the integration test `test_grant_matrix_separates_minting_from_redistribution`.

2. **SECURITY DEFINER boundary** — `dw.confirm_deposit` is the only writer that can `INSERT` into `core.balances` (a new user's first deposit creates the row); `dw.confirm_withdraw` and `dw.cancel_withdraw` are the only writers that can `UPDATE` `core.balances` outside the game-settlement path; they all run with `dw_writer` privileges and end with an audit-log emission.

3. **Three-layer human friction at confirm** — claim (cashier registers), in-game trade (real act outside the bot), confirm + 2FA modal "Type CONFIRM". A stolen cashier identity alone cannot drain the system; the in-game trade must visibly happen and discrepancies are detectable in audit logs and via dispute reports from users.

## Consequences

Positive:

- Game bots cannot accidentally (or maliciously, if compromised) mint balance. The blast radius of a compromised Luck token is bounded — at worst it could redistribute existing balances among users, but it cannot inflate the total in circulation.
- The treasury invariant `SUM(user balances) + treasury_balance + admin_swept_total == total_ever_deposited` becomes a property checkable in the database. Pinned in the property-based test `test_treasury_invariant_holds_under_concurrency`.
- The audit log is the single source of truth for "where did this gold come from / go to": every minting event traces to a `dw.confirm_deposit`; every destruction traces to a `dw.confirm_withdraw`.

Negative:

- Disputes that require refunding a user (after a confirmed transaction) must route through D/W; admins cannot directly UPDATE balance from a Luck operator console. Acceptable: the audit-trail discipline is the whole point.
- A future bot that needs to mint balance (e.g., a tournament prize-pool seeder) would have to either (a) route through D/W, or (b) get its own role added to the EXECUTE grant on `dw.confirm_deposit`. The latter requires this ADR to be revisited.

## Alternatives considered

- **Allow each game bot to mint refunds directly**: rejected because every bot would become a candidate point of compromise for inflation attacks. The role-separation discipline is structurally simpler.
- **Move minting outside SECURITY DEFINER, into the bot Python code**: rejected because Python-level checks can be bypassed if the bot's connection role gains privileges, while SECURITY DEFINER fns stop at the function boundary regardless of caller privileges.

## References

- D/W design spec §6.1 ("the economic frontier")
- D/W design spec §6.4 (anti-fraud vectors and mitigations)
- Migration `0006_dw_deposit_tickets.py` and `0007_dw_withdraw_tickets.py` (the SECURITY DEFINER fns)
- Integration test `tests/integration/dw/test_grant_matrix.py`
