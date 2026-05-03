# ADR 0012 — Deposit/Withdraw modal carries no server-side draft state

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

When a user runs `/deposit` (or `/withdraw`), Discord presents a modal with four fields: amount, character name, region, faction. The user fills the modal, hits Submit, and the bot creates a deposit/withdraw ticket.

A natural design would be to persist a "draft" row in `dw.*_tickets` the moment the user opens the modal, so that a partial submission (the user opens the modal, types an amount, then closes it) leaves a recoverable trace. This pattern is common in webapps.

The problem is that Discord modals are single-shot: the bot only sees the inputs at the moment the user clicks Submit. There is no `on_open` or `on_change` event. A partial fill is invisible to the bot.

## Decision

**The deposit/withdraw modal is stateless: nothing is persisted server-side until the user clicks Submit.** The modal is a thin form that maps directly to a single SECURITY DEFINER call (`dw.create_deposit_ticket` / `dw.create_withdraw_ticket`). The transaction either succeeds and produces a ticket, or fails and produces a typed-Pydantic validation error embed; in either case there is no half-built database row.

Concretely:

- `DepositModal` and `WithdrawModal` (`deathroll_deposit_withdraw/views/modals.py`) are pure `discord.ui.Modal` subclasses. They hold no instance state beyond the four `TextInput` fields.
- `on_submit` reads the four fields, builds a Pydantic input object (`OpenDepositInput` / `OpenWithdrawInput`) with `extra='forbid'` and field-level validators (region in `EU/NA`, faction in `Alliance/Horde`, amount in range, etc.), and dispatches to the SECURITY DEFINER fn via the orchestration helper.
- A validation failure renders an ephemeral error embed; the user can re-run `/deposit` to start fresh. There is no "resume draft" path because there is no draft.
- The withdraw flow does lock the user's balance at create time (so two parallel `/withdraw` calls can't double-spend); but the lock+ticket creation is a single SECURITY DEFINER transaction. There is still no draft state.

## Consequences

Positive:

- Vastly simpler code — no draft cleanup worker, no expired-draft GC, no UI for "resume your draft", no persisted secret hidden inside a draft row.
- No leakage path for a user to discover that "even after I cancelled, my draft was visible to admins". The only persisted ticket states are `open`, `claimed`, `confirmed`, `cancelled` — all post-Submit.
- Race conditions are bounded: the user either has a ticket or doesn't. There is no third state.

Negative:

- A user who fills 3 of 4 fields, accidentally closes the modal, and re-runs `/deposit` retypes everything. Acceptable — modals are short, and Discord's autofill assists name/region after the first submission.
- We cannot show admins "users currently in the act of opening a ticket" as a metric; only the post-Submit ticket counter is meaningful. Acceptable for v1.

## Alternatives considered

- **Persist a `draft` state in `dw.*_tickets` with TTL=10 min**: rejected because it adds a worker, a state, and a leakage path with no payoff in user experience (the modal is short).
- **Use a `discord.ui.View` with persistent buttons instead of a modal, so each field is a separate interaction**: rejected because the per-step latency feels worse than the all-at-once modal, and the multi-step state would still need persistence to survive bot restarts.

## References

- D/W design spec §5.5 (modal validation)
- `deathroll_deposit_withdraw/views/modals.py` (the modal implementations)
- Migration `0006_dw_deposit_tickets.py` (the create-ticket SDF transaction)
- Pydantic input contracts at `deathroll_core/models/dw_pydantic.py`
