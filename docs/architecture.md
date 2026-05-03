# DeathRoll — Architecture overview

> **Status**: stub (Story 1.5). Polished from the design specs throughout Luck implementation.

## Platform topology

TODO: Story 1.5 / 12.x — a high-level diagram. Three Discord bots (Luck, Deposit/Withdraw, Poker) sharing a single PostgreSQL DB; per-bot Postgres roles; SECURITY DEFINER fns as the only mutation paths; Prometheus + Loki + Grafana observability layer.

## Repository layout

TODO: Story 1.5 — link to `adr/0001-monorepo-layout.md`. Brief recap of the four Python packages (`deathroll_core`, `deathroll_luck`, `deathroll_deposit_withdraw`, `deathroll_poker`) and the shared `ops/`, `docs/`, `tests/` directories.

## Data flow — game outcome

TODO: Story 6.x — sequence diagram for a typical bet: slash command → modal → `dw.confirm_*` (no, wait, this is Luck) → `luck.settle_bet_*` SECURITY DEFINER fn → `core.balances` updated → audit log row → embed reply.

## Data flow — economic frontier

TODO: Story 13.x — link to D/W spec §6.1 (the economic frontier discipline), `tickets/deposit-flow.md`, `tickets/withdraw-flow.md`. Explain how the D/W bot is the only minter/destroyer of `core.balances` rows.

## Provably fair

TODO: Story 8.x — link to `provably-fair.md`. Explain the HMAC-SHA512 commit-reveal model and the per-user seed lifecycle.

## References

- ADR 0001 (monorepo layout)
- Luck design spec §2 (architecture)
- D/W design spec §2 (architecture)
