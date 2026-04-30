# ADR 0001 — Monorepo layout for the three GoldRush bots

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-04-29 |
| Author | Aleix |

## Context

GoldRush will run three Discord bots: Luck (games), Deposit/Withdraw (banking), and Poker (future). All three settle in WoW Gold against a single PostgreSQL database. They share core concerns: balance management, audit logging, provably-fair primitives, embed builders, security helpers, ORM models, configuration loading.

We considered two repository layouts:

- **Multi-repo** — one Git repository per bot; the shared `goldrush_core` package lives in its own repository and is consumed via PyPI, a private package index, or git URLs.
- **Monorepo** — one Git repository contains every bot package and the shared `goldrush_core` package; one Alembic migration history covers every schema across all bots.

## Decision

We use a **monorepo**.

The repository contains four Python packages (`goldrush_core`, `goldrush_luck`, `goldrush_deposit_withdraw`, `goldrush_poker`), one shared `ops/` directory for Docker/Alembic/scripts/observability, one shared `docs/` directory, and one shared `tests/` directory.

## Consequences

Positive:

- Cross-cutting changes (e.g., a new column in `core.balances` that affects both Luck and D/W) land in a single atomic commit.
- One Alembic history avoids migration drift between bots.
- Refactoring `goldrush_core` is safe because every consumer is in the same repository and CI exercises it together.
- One `pyproject.toml` and one lockfile keep dependency versions consistent across all three bots.
- `docs/` lives in one place; specs and ADRs are easy to find regardless of which bot they describe.

Negative:

- The repository name `GoldRush-Luck` no longer reflects the breadth of its contents. Acceptable: rename later if the platform grows beyond the current scope.
- A second public repository (`GoldRush-Deposit-Withdraw`) was created during a brief period when multi-repo was being considered. It will be archived; this ADR documents that the canonical repository is the monorepo.

## Alternatives considered

- **Multi-repo with `goldrush-core` as a published package**: rejected because cross-bot schema changes would require coordinated PRs across three repositories and version-bump dance for every internal API change. Friction outweighs the cosmetic benefit of cleaner repo boundaries.
- **Multi-repo with `goldrush-core` as a git submodule**: rejected because submodules are operationally fragile; updating them is easy to forget; and migrations would still need a coordinated owner.

## References

- Luck design spec §2 (architecture)
- D/W design spec §2 (architecture)
