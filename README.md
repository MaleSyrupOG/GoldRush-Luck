# GoldRush

A Discord-hosted casino platform settling in World of Warcraft Gold (`G`). Three bots share a single PostgreSQL database and a common security and audit foundation:

- **GoldRush Luck** — provably-fair casino games (paused while collaborator finalises Round 2 scope).
- **GoldRush Deposit/Withdraw** — the economic frontier; the only component that can credit or debit user balances outside game outcomes.
- **GoldRush Poker** — dedicated poker bot (future).

This is a monorepo. Each bot is an independent Python process with its own Discord application, token, and DB role; all three share `goldrush_core/` for balance, audit, fairness, security helpers, embed builders, and ORM models.

## Repository structure

```
goldrush/
├── goldrush_core/                     # shared business logic (framework-agnostic)
├── goldrush_luck/                     # casino games bot
├── goldrush_deposit_withdraw/         # banking bot (economic frontier)
├── goldrush_poker/                    # placeholder for the future poker bot
├── ops/                               # docker, alembic, scripts, observability
├── docs/                              # specifications, runbook, ADRs, security
│   └── superpowers/specs/             # design specs and implementation plans
├── tests/                             # unit, integration, property, e2e
└── .github/workflows/                 # CI
```

## Source of truth

- **Designs**: `docs/superpowers/specs/2026-04-29-goldrush-luck-v1-design.md`, `docs/superpowers/specs/2026-04-29-goldrush-dw-v1-design.md`.
- **Implementation plans (with progress trackers)**: corresponding `*-implementation-plan.md` files.
- **Operational runbook**: `docs/runbook.md` (built incrementally).
- **Architecture decisions**: `docs/adr/` (each major decision is an immutable ADR).

## Getting started

This repository is in early bootstrap. The implementation plan progress trackers in `docs/superpowers/specs/` are the canonical record of what is done, in progress, and pending.

For local development:

```bash
make setup          # provision uv venv + dev dependencies
make test           # run unit + integration tests
make lint           # ruff check + format check
make type           # mypy --strict on critical packages
make run-dev-luck   # run the Luck bot against a local Postgres
make run-dev-dw     # run the D/W bot against a local Postgres
```

For deployment, see `docs/operations.md`.

## Stack

- Python 3.12
- discord.py 2.x
- PostgreSQL 16 with role-isolated schemas (`core`, `fairness`, `luck`, `dw`)
- asyncpg + SQLAlchemy 2.0 async
- Alembic (shared migrations)
- pydantic v2, pydantic-settings, structlog, Pillow, prometheus-client
- uv for dependency management

## Author

Aleix.

## License

See `LICENSE`.
