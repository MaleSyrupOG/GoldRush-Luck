# GoldRush — documentation index

This directory is the single source of truth for the GoldRush platform's design, security, operations, and progress.

## Source-of-truth artefacts

| Document | Purpose |
|---|---|
| [`superpowers/specs/2026-04-29-goldrush-luck-v1-design.md`](superpowers/specs/2026-04-29-goldrush-luck-v1-design.md) | Luck bot design (v1.1, locked) |
| [`superpowers/specs/2026-04-29-goldrush-luck-v1-implementation-plan.md`](superpowers/specs/2026-04-29-goldrush-luck-v1-implementation-plan.md) | Luck bot implementation plan with progress tracker |
| [`superpowers/specs/2026-04-29-goldrush-dw-v1-design.md`](superpowers/specs/2026-04-29-goldrush-dw-v1-design.md) | Deposit/Withdraw bot design (v1.0, locked) |
| [`superpowers/specs/2026-04-29-goldrush-dw-v1-implementation-plan.md`](superpowers/specs/2026-04-29-goldrush-dw-v1-implementation-plan.md) | **D/W bot implementation plan with progress tracker — current focus** |

## Operational documents (built incrementally during implementation)

| Document | Purpose |
|---|---|
| `architecture.md` | High-level architecture overview, polished from the specs |
| `security.md` | Twelve-pillar security model, threat model, redaction rules |
| `provably-fair.md` | User-facing explanation of the HMAC-SHA512 fairness scheme |
| `operations.md` | Deploy procedures, VPS setup, day-to-day ops |
| `runbook.md` | Incident playbooks (bot down, exploit suspected, dispute spike, etc.) |
| `backup-restore.md` | Backup script behaviour, restore procedure, drill cadence |
| `observability.md` | Prometheus metrics, Grafana dashboards, Alertmanager rules |
| `dr-plan.md` | Disaster recovery procedures and RTO/RPO targets |
| `release-process.md` | How to publish a versioned release |
| `secrets-rotation.md` | Per-secret rotation procedure |
| `responsible-gambling.md` | Explicit decision: no v1 RG features; rationale and revisit triggers |
| `onboarding.md` | 30-minute ramp-up guide for new contributors |
| `changelog.md` | Per-bot semver release notes |
| `compliance.md` | Retention policy, financial-records guidance |

## Architecture Decision Records

Every major decision is captured as an immutable ADR in `adr/`. ADRs follow the standard template (Status / Context / Decision / Consequences / Alternatives).

## Per-game and per-ticket technical sheets

- `games/` — one file per Luck game with rules, payouts, edge, fairness decoding.
- `tickets/` — D/W flows: deposit, withdraw, cashier onboarding, ticket lifecycle, treasury, disputes.

## Public verifier

`verifier/` contains the open-source HMAC-SHA512 verification scripts (`verify.js`, `verify.py`) plus example test vectors. Any user can clone these and recompute any past outcome locally.

## API / sequence diagrams

`api/` holds mermaid diagrams for the most important flows: bet lifecycle, deposit/withdraw flow, fairness rotation.
