# Ticket lifecycle (state machine reference)

The formal model for what a deposit or withdraw ticket can do, and which actor can do it. This is a technical reference for cashiers, admins, and anyone reading the audit log.

---

## 1. The states

Both deposit and withdraw tickets share a four-state lifecycle:

```
open ──claim──▶ claimed ──confirm──▶ confirmed
  │                │
  │                └──release──▶ open
  │                │
  └────cancel──────┴────cancel──▶ cancelled
```

| State | Meaning |
|---|---|
| `open` | Ticket created, no cashier claimed it yet. The user is waiting. |
| `claimed` | A cashier has called `/claim`. They are now responsible for following through. |
| `confirmed` | Terminal. The cashier has run `/confirm` (with 2FA modal `CONFIRM`); the SECURITY DEFINER fn has committed; balance and audit log are updated. |
| `cancelled` | Terminal. The ticket was aborted before confirm. For a withdraw, the user has been refunded `amount + fee`. |

Terminal states (`confirmed`, `cancelled`) are immutable — no transitions out. The trigger-driven hash chain on `core.audit_log` enforces this at the database level.

---

## 2. Transitions and the actor allowed

| From | To | Verb (slash command) | Actor |
|---|---|---|---|
| `open` | `claimed` | `/claim` | A cashier with state=`online` and a matching region/faction character |
| `claimed` | `open` | `/release` | The claimer (the cashier in `claimed_by`) |
| `claimed` | `open` | `/admin-force-release` | An admin (any) |
| `claimed` | `confirmed` | `/confirm` (2FA `CONFIRM`) | The claimer |
| `open` | `cancelled` | `/cancel-mine` | The ticket owner |
| `claimed` | `cancelled` | `/cancel` | The claimer |
| `open` or `claimed` | `cancelled` | `/admin-force-cancel-ticket reason:<text>` | An admin (any) |
| any non-terminal | `open` | `claim_idle` worker auto-release | The bot (worker) |

Every transition writes a row in `core.audit_log` with the action name, the ticket UID, and the actor's discord_id.

---

## 3. The SECURITY DEFINER fns enforcing each transition

Each transition is implemented as a SECURITY DEFINER function. The bot can ONLY transition tickets through these fns; the `deathroll_dw` role has no direct UPDATE on `dw.*_tickets`.

| Transition | Fn |
|---|---|
| create open ticket | `dw.create_deposit_ticket(...)` / `dw.create_withdraw_ticket(...)` |
| open → claimed | `dw.claim_ticket(ticket_type, ticket_uid, cashier_id)` |
| claimed → open | `dw.release_ticket(ticket_type, ticket_uid, actor_id)` |
| claimed → confirmed | `dw.confirm_deposit(...)` / `dw.confirm_withdraw(...)` |
| any non-terminal → cancelled | `dw.cancel_deposit(...)` / `dw.cancel_withdraw(...)` |

Each fn:

1. Locks the ticket row `FOR UPDATE`.
2. Validates the source state: e.g., `claim` rejects a non-`open` ticket with `RAISE EXCEPTION`.
3. Validates the actor: the right cashier id (`claim_ticket` requires `online`+region match; `release_ticket` rejects `wrong_cashier`).
4. Performs the state change inside the transaction.
5. Emits the audit row.
6. (For confirm) updates `core.balances` rows under the same transaction.

If any step fails, the whole transaction rolls back. There is no half-applied state.

---

## 4. The two terminal states are NOT the same

`confirmed` and `cancelled` look symmetric in the diagram, but they have very different consequences:

| | `confirmed` | `cancelled` |
|---|---|---|
| User balance impact (deposit) | Credited the amount | No change |
| User balance impact (withdraw) | No change at confirm (already debited at create) | Refunded `amount + fee` |
| Treasury impact (withdraw) | Credited the fee (already at create; audit row at confirm) | Fee refunded back to user |
| Audit-log row | `deposit_confirmed` / `withdraw_confirmed` | `deposit_cancelled` / `withdraw_cancelled` (+ fee-refund audit for withdraw) |
| Reversibility | Only via `disputes.md` flow + admin treasury operations | None needed (state is already pre-ticket) |

A `cancelled` ticket is effectively a no-op: the system is in the same place as if the ticket never existed. A `confirmed` ticket has moved gold; reversing it requires the dispute mechanism.

---

## 5. The auto-release worker

`claim_idle` is a background worker that runs every 60 s. It picks tickets in state `claimed` whose `claimed_at` is older than `CLAIM_IDLE_TIMEOUT_SECONDS` (default 1 800 s = 30 min) AND whose private channel has had no messages for the same window. For each, it calls `dw.release_ticket(actor_id = claimed_by)`. The ticket transitions back to `open` and the `cashier-alerts` ping fires again, attracting a different cashier.

> **Implementation note**: pre-Story 14.5 this worker called `dw.release_ticket(actor_id = 0)`, which the SDF rejected with `wrong_cashier` because `actor_id` must equal `claimed_by`. The worker silently failed in production. The fix (committed during Epic 14 testing) is documented in `tests/integration/dw/test_claim_idle_worker.py`. See `disputes.md` if you have a long-stuck ticket from before that fix.

---

## 6. The cashier-status idle worker

Different from `claim_idle`: `cashier_idle` (5 min cadence) auto-transitions a cashier from `online` to `offline` if their `cashier_status.last_action_at` is more than `CASHIER_IDLE_TIMEOUT_SECONDS` (default 1 800 s) old. This emits the `cashier_status_offline_expired` audit row. It does NOT release any tickets they've claimed — that's `claim_idle`'s job.

---

## 7. The two terminal-state guards

Two database-level invariants:

1. **`core.audit_log` is append-only.** A trigger (`core.audit_log_immutable`, migration 0001) raises an exception on any UPDATE or DELETE on `core.audit_log`, even by the `deathroll_admin` role. The hash chain (HMAC-SHA256 over each row) is verifiable end-to-end.

2. **Terminal ticket states are immutable.** Each SDF starts with `IF state IN ('confirmed', 'cancelled') THEN RAISE EXCEPTION 'already terminal'; END IF;`. A bug in the bot Python code that tried to call `claim_ticket` on a `confirmed` row would surface as a typed Pydantic exception rather than mutating the row.

Both are integration-tested:

- `test_audit_log_update_rejected_by_trigger` — the audit-log immutability guard.
- `test_terminal_state_rejects_transitions` — every terminal state, every transition verb, every ticket type. Should reject across the board.

---

## 8. Reading the audit log for a single ticket

The `core.audit_log` rows for a single ticket can be filtered by `target_ref` (the canonical `GRD-XXXX` UID):

```sql
SELECT id, action, actor_id, payload, created_at
FROM core.audit_log
WHERE target_ref = 'GRD-A1B2'
ORDER BY id ASC;
```

A typical happy-path deposit produces three rows:

```
1. deposit_ticket_opened    actor=user_id            payload={amount, char, region, faction}
2. ticket_claimed           actor=cashier_id         payload={ticket_uid, cashier_char}
3. deposit_confirmed        actor=cashier_id         payload={ticket_uid, amount, balance_after}
```

A cancelled withdraw would show:

```
1. withdraw_ticket_opened   actor=user_id            payload={amount, fee, char, region, faction}
2. (optional) ticket_claimed
3. (optional) ticket_released
4. withdraw_cancelled       actor=user_id|cashier|admin  payload={ticket_uid, reason}
5. transfer_out (treasury → user)  payload={amount=fee, source=ticket_uid}
```

The `/admin-view-audit user:@<u>` command renders the same data inline as a paginated embed.

---

## 9. UID format

Tickets use the `GRD-XXXX` UID format inherited from the live DeathRoll guild's pre-bot convention. The 4-char component is base36 (`0-9A-Z`). Total namespace is `36^4 = 1 679 616` UIDs per ticket-type-per-week. The bot enforces uniqueness inside `dw.*_tickets.uid_slug`; collisions trigger a deterministic re-roll.

The `GRD-` prefix is short for "GoldRush Deposit" — a relic of the platform's original name. It is preserved deliberately because operators have it in muscle memory and because changing it would break audit-log searches that pre-date the rename. See spec v1.1 for the formal documentation of this format and ADR 0013.

---

## 10. Quick state diagnosis

```sql
SELECT uid_slug, state, claimed_by, created_at, claimed_at, confirmed_at, cancelled_at
FROM dw.deposit_tickets
WHERE uid_slug = 'A1B2';
```

If the ticket isn't in any state you expect, look for:

- A `claim_idle` auto-release event in the audit log.
- An admin `force-cancel` action.
- An ongoing `claim` transaction (rare, only visible if you query inside a long-running transaction).

The `state` field is always the source of truth — every observation agrees with it because every mutation routes through a SECURITY DEFINER fn that updates `state` and writes the audit row inside the same transaction.

---

## 11. References

- `deposit-flow.md`, `withdraw-flow.md` — user-facing
- `cashier-onboarding.md` — cashier-facing
- `disputes.md` — what to do when a ticket has gone wrong post-confirm
- ADR 0011, 0012, 0013, 0014, 0015, 0016, 0017
- D/W design spec §5.4 (state machine), §5.5 (modal validation), §5.7 (cashier system)
