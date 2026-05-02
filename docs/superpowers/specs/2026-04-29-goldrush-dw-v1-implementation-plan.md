# GoldRush Deposit/Withdraw v1 — Implementation Plan (Epics, Stories, Acceptance Criteria)

| Field | Value |
|---|---|
| **Document version** | 1.0 |
| **Date** | 2026-05-01 |
| **Author** | Aleix |
| **Repository** | <https://github.com/MaleSyrupOG/GoldRush-Luck> (monorepo) |
| **Status** | Active — drives implementation work |
| **Source spec** | `2026-04-29-goldrush-dw-v1-design.md` |

---

## 📍 Progress Tracker (PRIMARY INDEX — keep updated at all times)

> **This file is the source of truth for D/W implementation progress.**
> Before any work session starts, read this section first.
> When any AC completes, edit the relevant `- [ ]` to `- [x]` in the same PR.
> When a story enters/exits a state, update its `Status:` line.
> When a story is blocked, set `Status: Blocked` and add `Blocked: <reason>`.

### Current state

| Field | Value |
|---|---|
| **Active phase** | Phase 4 — Money flows (Epics 5 / 6 in progress) |
| **Active epic** | Epic 5 (deposit) and Epic 6 (withdraw) running in parallel |
| **Active story** | Stories 5.1, 5.2, 6.1, 6.2 done; next pair is Story 5.3 (cashier alert) then 5.4 / 6.3 (claim/release/cancel) |
| **Last commit** | `6952777` (Story 4.5; Epic 4 closed) → Stories 5.1-5.2-6.1-6.2 commit pending |
| **Next milestone** | Close Stories 5.4 / 6.3 (lifecycle commands) and 5.5 / 6.4 (confirm + 2FA) so the bot can run a full deposit / withdraw end-to-end |
| **Overall progress** | 33 / 78 stories done · 5 / 15 epics done · Epics 5 / 6 in progress (4 / 9 combined) |

### Epic-level status

| Epic | Title | Status | Stories Done |
|---|---|---|---|
| 1 | Foundation extensions | Done | 3 / 3 |
| 2 | Database schema additions | Done | 12 / 12 |
| 3 | Core services & models | Done | 4 / 4 |
| 4 | Bot skeleton | Done | 5 / 5 |
| 5 | Deposit flow | In Progress | 2 / 5 |
| 6 | Withdraw flow | In Progress | 2 / 4 |
| 7 | Cashier system | Pending | 0 / 3 |
| 8 | Background workers | Pending | 0 / 6 |
| 9 | Disputes & blacklist | Pending | 0 / 3 |
| 10 | Admin commands | Pending | 0 / 8 |
| 11 | Observability | Pending | 0 / 3 |
| 12 | Operations & deploy | In Progress (brought forward) | 5 / 6 |
| 13 | Documentation final pass | Pending | 0 / 4 |
| 14 | Testing | Pending | 0 / 8 |
| 15 | Production verification & launch | Pending | 0 / 4 |

### Decision log additions during implementation

(Track here any deviations from the spec that arise during implementation, with date and ADR reference.)

| Date | Story | Decision | ADR |
|---|---|---|---|
| _none yet_ | | | |

### Blockers and notes

(Track here any cross-cutting blockers, dependency issues, or notes for future sessions.)

| Date | Note |
|---|---|
| 2026-05-01 | Pillow 11.0.0 has no prebuilt Windows wheel for Python 3.14. `.python-version` pinned to 3.12 in repo root. Local devs on Windows + Python 3.14 should `uv python install 3.12` once; uv then uses 3.12 automatically. CI already uses 3.12. No code change needed; tracked here so future onboarding does not surface this as new bug. |
| 2026-05-01 | Decision: bring forward Epic 12 (Operations & deploy) before Epic 2 (DB schema additions) so the VPS infrastructure is set up first. Epic 2 stories will then run their Alembic migrations against the real Postgres on the VPS (or via SSH tunnel for local dev). This out-of-order execution is intentional — the rest of the plan otherwise stands. |
| 2026-05-01 | Decision: bring forward Luck Story 13.3 (vps_first_setup.sh), 13.4 (backup.sh + cron), 13.5 (restore.sh) as part of the same infrastructure batch. They are foundational for both bots. The Luck plan will reference these as already done when it resumes. |
| 2026-05-01 | VPS infrastructure deployed and verified live on 91.98.234.106. Postgres healthy with all 5 schemas and 4 active roles (poker disabled). Placeholder D/W container running healthy. GPG backup key fingerprint `59CC31BED2A9557C8E6842723C40E9BEA65AF9B8` recorded by Aleix off-VPS. Cron entry for backup not yet installed (deferred until first real data exists, then Story 12.6 backup drill validates the cycle). |
| 2026-05-01 | Epic 2 (12 migrations + SECURITY DEFINER fns) applied to the live VPS Postgres. core has 4 tables (users, balances, audit_log, audit_chain_state) and 2 SECURITY DEFINER functions (audit_log_immutable, audit_log_insert_with_chain). dw has 9 tables and 18 SECURITY DEFINER functions. Treasury seeded at discord_id=0. Local end-to-end smoke test verified: deposit cycle (50,000 G credited) and withdraw cycle (30,000 G with 600 G fee captured to treasury, amount_delivered=29400 persisted). Permission boundary tests passed: goldrush_luck cannot UPDATE core.balances or INSERT core.users; audit_log triggers reject UPDATE/DELETE. Bot rebuilt and restarted on VPS with the new image (includes psycopg2-binary for alembic + ops/alembic/ baked in for deploys). |
| 2026-05-01 | Outstanding for Epic 14 (testing): testcontainers-based integration tests for the migrations and SECURITY DEFINER paths (concurrency, idempotency, treasury invariant property test). Migrations themselves validated by smoke tests; tests will land alongside Python facades in Epic 3 / 14. |
| 2026-05-02 | Story 3.3 done. `goldrush_core/embeds/dw_tickets.py` adds 16 embed builders (14 from spec §5.6 + 2 helpers from the visual contract). Builders are pure functions returning `discord.Embed`; no DB / network dependence. The visual contract from `reference_deposit_ticket_ux.md` (5-state colour-coded deposit lifecycle, anti-phishing warning, NA→US label, comma-separated amounts) is fully encoded. Withdraw open embed surfaces `amount`/`fee`/`amount_delivered` upfront; withdraw cancel announces `REFUNDED` in the title. 52 snapshot tests in `tests/unit/core/test_dw_embeds.py` guard the visual contract; full unit suite 154 / 154 green; ruff + mypy strict clean. |
| 2026-05-02 | Stories 5.1, 5.2, 6.1, 6.2 done in a paired commit (the deposit/withdraw open flows are atomic units that can't be split mid-way because `dw.create_*_ticket` requires a thread_id at NOT NULL insert time). New: `goldrush_deposit_withdraw/tickets/orchestration.py` (typed-outcome wrappers `open_deposit_ticket` / `open_withdraw_ticket`); `goldrush_deposit_withdraw/cogs/deposit.py` and `withdraw.py` rewritten with the slash commands; `DwBot.rate_limiters` dict (1/60s for both); `dw_manager.py` returns are now `cast()` so mypy strict passes through the cog import chain. 14 new tests; full suite 296 / 296; ruff + mypy strict clean. |
| 2026-05-02 | Story 4.5 done. Epic 4 closed. `goldrush_core/balance/cashier_roster.py` adds the live roster query; `RosterSnapshot` (frozen) buckets cashiers by region + on-break + offline count; a cashier with chars in multiple regions appears in each region's bucket. `online_cashiers_live_embed` refactored to take the snapshot directly (3 existing tests + 1 new test updated). `goldrush_deposit_withdraw/cashiers/live_updater.py` ships `tick(pool, bot, channel_id)` (single iteration, persists message id in `dw.dynamic_embeds[embed_key='online_cashiers']`, self-heals on NotFound) and `OnlineCashiersUpdater` (cancellable asyncio loop with idempotent start, awaitable stop, broad-except wrappers around tick so a transient error doesn't kill the loop). `DwBot.on_ready` resolves the channel id from `dw.global_config.channel_id_online_cashiers` and spins up the updater; `close_pool` shuts it down. 14 new tests + 1 modified test surface; full suite 253 / 253; ruff + mypy strict clean. |
| 2026-05-02 | Story 4.4 done. `goldrush_deposit_withdraw/welcome.py` adds the reconciler for `dw.dynamic_embeds` rows `how_to_deposit` and `how_to_withdraw`. `WelcomeDefault` (frozen) carries the canonical seed title/description; `DEFAULT_WELCOMES` is a tuple of two seeds. `reconcile_welcome_embed(pool, bot, *, embed_key, fallback_channel_id, ...)` handles single-key reconciliation; `reconcile_welcome_embeds(pool, bot)` orchestrates both managed keys, resolving channel ids from `dw.global_config.channel_id_<embed_key>`. Outcomes: `posted` (first run), `edited` (idempotent re-run), `reposted` (self-heal after admin deletes the Discord message), `skipped` (no channel id available pre-`/admin setup`). The reconciler is wired into `DwBot.on_ready` with a broad-except so a DB hiccup is non-fatal — next on_ready retries. 11 new tests (orchestrator + every single-key branch + idempotency property + self-heal); full suite 239 / 239; ruff + mypy strict clean. |
| 2026-05-02 | Story 4.3 done. `goldrush_core/balance/account_stats.py` adds `AccountStats` (frozen) + `fetch_account_stats(executor, *, discord_id) -> AccountStats | None` (single-row JOIN over core.users / core.balances / confirmed dw.deposit_tickets / confirmed dw.withdraw_tickets, all aggregates COALESCEd to 0). `goldrush_core/embeds/account.py` adds `account_summary_embed`, `no_balance_embed` (shared with Luck per spec §5.6), `help_embed` (with `HELP_TOPICS` ordered dict). `goldrush_deposit_withdraw/cogs/account.py` ships the real `/balance` and `/help` slash commands — both ephemeral, `/help` with autocomplete choices for the four topics. Unknown topics fall back to the list view rather than raising. `_resolve_how_to_deposit_mention` does best-effort name lookup until Story 10.x reads channel ids from `dw.global_config`. 17 new tests; full suite 228 / 228; ruff + mypy strict clean. |
| 2026-05-02 | Story 4.2 done. Six cog skeletons created under `goldrush_deposit_withdraw/cogs/` (account, admin, cashier, deposit, ticket, withdraw); each exposes the `async def setup(bot)` contract. `EXTENSIONS` tuple populated; `setup_hook` now loads all six. `DwBot.on_ready` overridden — calls `bot.tree.sync(guild=discord.Object(id=settings.guild_id))` for instant per-guild sync; logs `user_id`, `guild_id`, `command_count`. 10 new tests (1 base + 6 parametrized cog-contract + 3 functional); full suite 211 / 211; ruff + mypy strict clean. |
| 2026-05-02 | Story 4.1 done. `goldrush_core/config/__init__.py` adds `CoreSettings` + `DwSettings` (pydantic-settings v2; secrets typed as SecretStr; reads from `.env.shared` + `.env.dw` in dev). `goldrush_core/logging/__init__.py` adds `setup_logging(level, *, format)` with a structlog + stdlib pipeline that toggles between JSON (production) and ConsoleRenderer (local dev). `goldrush_deposit_withdraw/client.py` adds `DwBot` (commands.Bot subclass) + `build_bot(settings, *, pool_factory=None)`; the pool factory is injectable for tests. `goldrush_deposit_withdraw/healthcheck.py` rewritten — opens a 1-conn pool with 3-second timeout, runs `SELECT 1`, exits 0 only when the result is exactly 1; every failure path (missing DSN, factory raises, timeout, wrong value, exception) maps to exit 1. `goldrush_deposit_withdraw/__main__.py` rewritten — loads settings, configures logging, runs the bot. asyncpg added to mypy `ignore_missing_imports` (no py.typed marker upstream). 25 new tests; full unit suite 201 / 201; ruff + mypy strict clean. |
| 2026-05-02 | Story 3.4 done. Epic 3 closed. `goldrush_deposit_withdraw/setup/channel_factory.py` implements `setup_or_reuse_channels(guild, *, cashier_role_id, admin_role_id, dry_run=False, persist=None) -> SetupReport`. Idempotent name+parent matching; spec §5.3 permission matrix encoded per channel and per role; `manage_threads` substituted for the spec's "View Private Threads" because discord.py 2.4.0 does not expose `view_private_threads` (folded into manage_threads upstream). Persistence decoupled via async callback; module is DB-agnostic. Channel naming uses spec-canonical (`#cashier-alerts`, `#how-to-deposit`); the live server's renamed equivalents (`#cashier-requests`, etc.) will be re-linked via `/admin set-channel <key>` once Story 10.x lands — flagged inline. 23 tests in `tests/unit/dw/test_channel_factory.py`; full unit suite 177 / 177 green; ruff + mypy strict clean. |

---

## 🔗 Related D/W documentation (this is the relational hub)

> Whenever a new doc related to the D/W bot is created or substantially edited, **add a link here** so this plan stays the single point of entry for any D/W work session.

### Source-of-truth design

| Path | Role |
|---|---|
| [`2026-04-29-goldrush-dw-v1-design.md`](./2026-04-29-goldrush-dw-v1-design.md) | The locked v1 design spec — the WHAT this plan implements |

### Architecture decision records (ADRs)

ADRs documenting D/W-specific architectural decisions. Each is immutable except for status updates.

| Path | Decision |
|---|---|
| [`../../adr/0001-monorepo-layout.md`](../../adr/0001-monorepo-layout.md) | Why all bots live in one repo (general, applies to D/W too) |
| `../../adr/0011-dw-as-economic-frontier.md` | _to be written in Story 13.2_ |
| `../../adr/0012-stateless-deposit-modal.md` | _to be written in Story 13.2_ |
| `../../adr/0013-private-threads-for-tickets.md` | _to be written in Story 13.2_ |
| `../../adr/0014-cashier-online-status-model.md` | _to be written in Story 13.2_ |
| `../../adr/0015-treasury-as-system-account.md` | _to be written in Story 13.2_ |
| `../../adr/0016-2fa-modals-for-money-ops.md` | _to be written in Story 13.2_ |
| `../../adr/0017-admin-setup-channel-creation.md` | _to be written in Story 13.2_ |

### Ticket and operational guides (`docs/tickets/`)

| Path | Purpose | Author of (story) |
|---|---|---|
| `../../tickets/deposit-flow.md` | User-facing guide: how a deposit works | Story 13.1 |
| `../../tickets/withdraw-flow.md` | User-facing guide: how a withdraw works | Story 13.1 |
| `../../tickets/cashier-onboarding.md` | Guide for new cashiers | Story 13.1 |
| `../../tickets/ticket-lifecycle.md` | Technical: state machine reference | Story 13.1 |
| `../../tickets/treasury-management.md` | Admin: how to sweep revenue | Story 13.1 |
| `../../tickets/disputes.md` | Admin: dispute workflow + examples | Story 13.1 |
| `../../tickets/compliance.md` | Legal / retention guide | Story 13.1 |

### Cross-cutting docs (sections specific to D/W)

| Path | D/W-specific section |
|---|---|
| [`../../security.md`](../../security.md) | D/W as economic frontier, anti-fraud table, treasury safeguards (Story 13.3) |
| [`../../runbook.md`](../../runbook.md) | D/W incident playbooks (Story 13.3) |
| [`../../observability.md`](../../observability.md) | D/W metrics, alerts, dashboards (Story 13.3) |
| [`../../operations.md`](../../operations.md) | D/W VPS setup, deploy procedure (Stories 12.3, 12.4, 12.5) |
| [`../../backup-restore.md`](../../backup-restore.md) | D/W backup verification (Story 12.6) |
| [`../../changelog.md`](../../changelog.md) | `dw-v1.0.0` release notes (Story 13.4) |

### Operational credentials (where, NOT what)

> Tokens and passwords are NEVER committed. This table only documents WHERE each credential is stored operationally.

| Credential | Local-dev location | Production location |
|---|---|---|
| D/W Discord bot token | `dwBotKeys.txt` on Aleix's local Desktop (gitignored, never committed) | `/opt/goldrush/secrets/.env.dw` on the VPS, mode 600, owned by `goldrush:goldrush` |
| Postgres `goldrush_dw` password | `.env` if running locally | `/opt/goldrush/secrets/.env.shared` on the VPS |
| `BUTTON_SIGNING_KEY` | `.env` | `.env.shared` |
| `AUDIT_HASH_CHAIN_KEY` | `.env` | `.env.shared` |
| GPG public key for backup encryption | n/a | `/opt/goldrush/secrets/backup-gpg-private.asc` on VPS; fingerprint also in Aleix's password manager |

### Public-facing repository

[`https://github.com/MaleSyrupOG/GoldRush-Luck`](https://github.com/MaleSyrupOG/GoldRush-Luck) — the monorepo. Despite the name still containing "Luck" (legacy), it hosts all three bots and the shared `goldrush_core`.

### Session logs

| Path | Coverage |
|---|---|
| [`../../sessions/2026-04-29_to_2026-05-01-session-log.md`](../../sessions/2026-04-29_to_2026-05-01-session-log.md) | Narrative of the brainstorm, spec, infrastructure bring-up, and Epic 2 migrations. Read this to retake context fast. |

### Sister-bot documentation

Cross-bot integration tests, schema co-evolution, and shared `goldrush_core` modules mean the Luck bot's docs are useful context.

| Path | Relation to D/W |
|---|---|
| [`./2026-04-29-goldrush-luck-v1-design.md`](./2026-04-29-goldrush-luck-v1-design.md) | Sister bot — shares DB tables `core.users`, `core.balances`, `core.audit_log` |
| [`./2026-04-29-goldrush-luck-v1-implementation-plan.md`](./2026-04-29-goldrush-luck-v1-implementation-plan.md) | Sister plan — Luck Epics 1-4 are prerequisites for D/W work |

---

## 0. How to read this document

The spec is _what_ the D/W bot does. This plan is _how_ and _in what order_ we build it. The plan is decomposed into **15 epics** containing concrete **stories** with explicit **acceptance criteria** (ACs).

This plan **assumes Luck's foundation work is already done** — the monorepo skeleton, Postgres compose, `core` schemas, audit log with hash chain, base CI, and shared `goldrush_core` modules are inherited from Luck's plan. Where D/W needs additions to a shared component, the story makes that explicit.

### Conventions

- **Story format:** "As X I want Y so that Z" + concrete description + ACs.
- **AC format:** observable, testable assertions.
- **Definition of Done** for every story:
  1. Code merged to `main` via PR with passing CI (lint, mypy strict, pip-audit, tests, coverage gates).
  2. All ACs verified.
  3. Relevant docs updated in the same PR.
  4. Commit message clean — no AI/generator attribution; author is Aleix.
- **Effort sizing:** S, M, L, XL.
- **Spec refs:** every story cross-references the relevant section(s) of the design spec.
- **Dependencies:** a story can only start when its dependencies are Done.

### Top-level phase ordering

```
Phase 1 (Foundation extensions)     → Epic 1, 2
Phase 2 (Core services & models)    → Epic 3
Phase 3 (Bot skeleton)              → Epic 4
Phase 4 (Money flows)               → Epic 5, 6  (deposit and withdraw can parallelise)
Phase 5 (Cashier system)            → Epic 7
Phase 6 (Background workers)        → Epic 8
Phase 7 (Disputes & blacklist)      → Epic 9
Phase 8 (Admin commands)            → Epic 10
Phase 9 (Observability)             → Epic 11
Phase 10 (Operations & deploy)      → Epic 12
Phase 11 (Documentation final pass) → Epic 13
Phase 12 (Testing)                  → Epic 14   (incremental throughout, finalised here)
Phase 13 (Launch)                   → Epic 15
```

Epics 5 and 6 can parallelise after Epic 4 is done. Epic 8 (background workers) can start as soon as the relevant tables exist (Epic 2). Documentation grows incrementally and gets a final pass in Epic 13.

---

## EPIC 1 — Foundation extensions for D/W

### Story 1.1 — Extend the monorepo skeleton with the `goldrush_deposit_withdraw` package

**Status:** Done (2026-05-01)

**As Aleix I want** the D/W package shaped from day one **so that** every later PR adds code, not directories.

**ACs:**
- [x] `goldrush_deposit_withdraw/{tickets,cashiers,commands,views,setup}/__init__.py` exist.
- [x] `goldrush_deposit_withdraw/__main__.py`, `client.py`, `healthcheck.py` placeholders exist.
- [x] `python -c "import goldrush_deposit_withdraw"` succeeds.
- [x] `tests/{unit,integration,property,e2e}/dw/` directories exist with `__init__.py` and a smoke test that imports the package.
- [x] `.gitignore` updated to exclude `dwBotKeys.txt`, `luckBotKeys.txt`, `*Keys.txt`, `*keys.txt`, `*.token`, `secrets/`, `.env`, `.env.*` (anywhere in the tree).
- [x] `.gitignore` audited: `git check-ignore -v dwBotKeys.txt` reports it ignored.

**Dependencies:** Luck Epic 1 (monorepo skeleton in place)
**Effort:** S
**Spec refs:** D/W §2.1

### Story 1.2 — D/W-specific dependencies (none new at runtime)

**Status:** Done (2026-05-01)

**As Aleix I want** D/W to share the runtime dependencies of Luck **so that** we keep one lockfile and one image base.

**ACs:**
- [x] No new entries needed in `pyproject.toml` runtime deps for D/W (all usage is covered by `discord.py`, `asyncpg`, `SQLAlchemy`, `pydantic`, `structlog`, `Pillow`, `prometheus-client`).
- [x] `Makefile` adds targets `run-dev-dw`, `test-dw-unit`, `test-dw-integration` (plus symmetric `test-luck-unit`, `test-luck-integration`, `test-cross-bot`).
- [x] `uv.lock` exists and reflects only the dependencies in `pyproject.toml` (initial generation; no dep changes during this story). 70 packages resolved.

**Dependencies:** Story 1.1
**Effort:** S
**Spec refs:** D/W §1.1
**Notes:** Initial `uv.lock` generated; `.python-version` pinned to 3.12 to side-step Python 3.14 / Pillow 11.0.0 wheel gap on Windows (see Blockers).

### Story 1.3 — CI pipeline extensions

**Status:** Done (2026-05-01)

**As Aleix I want** D/W coverage gates enforced in CI **so that** the bot's quality stays at fintech-grade.

**ACs:**
- [x] `.github/workflows/ci.yml` adds: `mypy --strict goldrush_deposit_withdraw`.
- [x] Coverage gates added: `goldrush_deposit_withdraw/tickets ≥ 95 %`, `goldrush_deposit_withdraw/cashiers ≥ 90 %`, `goldrush_deposit_withdraw/commands/admin_cog.py ≥ 90 %` (conditional on file existing), rest of `goldrush_deposit_withdraw ≥ 85 %`. Plus parallel gates for Luck (`goldrush_luck/games ≥ 90 %`, `goldrush_luck/admin ≥ 85 %`).
- [x] CI fails if any gate is missed (each `--cov-fail-under` exits non-zero on miss; the workflow step propagates the failure).
- [x] Cross-bot integration tests run on every PR (`tests/integration/cross_bot/`). Currently empty; the step succeeds vacuously and is wired to fail once tests land.

**Dependencies:** Story 1.1, Luck Story 1.3
**Effort:** S
**Spec refs:** D/W §8.3, §8.4, §8.5
**Notes:** Admin cog gate is wrapped with `if: hashFiles(...)` so it activates only once `admin_cog.py` exists (Story 11.1). All other gates run unconditionally and pass on the current empty-package state.

---

## EPIC 2 — Database schema additions

### Story 2.1 — Migration: `dw` schema and grants

**Status:** Done (2026-05-01) — applied to VPS Postgres in commit `31f826d` / `e3c91da`. Schema-level grants live in `ops/postgres/01-schemas-grants.sql` (init.sh). Per-table grants on `core.users`/`core.balances`/`core.audit_log` for `goldrush_dw` are added by migrations 0001 and 0002.

**As Aleix I want** the `dw` schema with correct grants in place **so that** every later migration adds tables without DDL ceremony.

**ACs:**
- [ ] Alembic migration `dw_001_create_schema_and_grants.py` creates schema `dw`.
- [ ] Grants per spec §3.1 applied for `goldrush_dw`, `goldrush_readonly`.
- [ ] Adds `INSERT, UPDATE` on `core.users`, `core.balances` to `goldrush_dw`.
- [ ] Adds `INSERT` on `core.audit_log` to `goldrush_dw`.
- [ ] Test: connect as `goldrush_dw`, run `INSERT INTO core.users` — succeeds.
- [ ] Test: connect as `goldrush_luck`, run `INSERT INTO core.users` — fails with permission denied.

**Dependencies:** Luck Epic 2 done
**Effort:** S
**Spec refs:** D/W §3.1

### Story 2.2 — Migration: `dw.deposit_tickets` and `dw.withdraw_tickets`

**Status:** Done (2026-05-01) — migration `0003_dw_tickets`. Both tables present on VPS with all CHECK constraints, indexes, and the shared terminal-state trigger.

**As Aleix I want** the two ticket tables with their indexes and terminal-state immutability triggers **so that** the lifecycle state machine is enforced at the DB level.

**ACs:**
- [ ] Migration creates both tables exactly per spec §3.2.
- [ ] All `CHECK` constraints enforced.
- [ ] All indexes created.
- [ ] Terminal-state-immutable trigger on each table; integration test: a `confirmed` row cannot be updated to `claimed`.
- [ ] Test: insert `claimed` row, update to `cancelled` — succeeds. Insert `confirmed` row, update to `claimed` — raises.
- [ ] SQLAlchemy ORM models added in `goldrush_core/models/dw.py`.

**Dependencies:** Story 2.1
**Effort:** M
**Spec refs:** D/W §3.2

### Story 2.3 — Migration: cashier tables

**Status:** Done (2026-05-01) — migration `0004_dw_cashier_tables`. Four tables present on VPS.

**As Aleix I want** the four cashier tables in place **so that** cashier registration, status, sessions, and stats can be implemented.

**ACs:**
- [ ] Migration creates `dw.cashier_characters`, `dw.cashier_status`, `dw.cashier_sessions`, `dw.cashier_stats` per spec §3.2.
- [ ] All check constraints and indexes per spec.
- [ ] Test: register two chars for one cashier; UNIQUE constraint blocks a third identical entry.
- [ ] SQLAlchemy ORM models added.

**Dependencies:** Story 2.1
**Effort:** S
**Spec refs:** D/W §3.2

### Story 2.4 — Migration: disputes, dynamic embeds, global config

**Status:** Done (2026-05-01) — migration `0005_dw_disputes_embeds_config`. global_config seeded with the 12 v1 default rows.

**As Aleix I want** the supporting tables in place **so that** disputes, editable embeds, and runtime config are persistable.

**ACs:**
- [ ] Migration creates `dw.disputes`, `dw.dynamic_embeds`, `dw.global_config` per spec §3.2.
- [ ] `dw.global_config` seeded with the 12 default keys from spec §3.2 (min/max amounts, fees, timeouts).
- [ ] Test: re-running the seed is idempotent (no duplicate rows).

**Dependencies:** Story 2.1
**Effort:** S
**Spec refs:** D/W §3.2

### Story 2.5 — Treasury system row

**Status:** Done (2026-05-01) — seeded by migration `0001_core_users_balances`. Row at `core.balances[discord_id=0]` confirmed on VPS.

**As Aleix I want** the treasury row to exist after migration **so that** every fee credit has a target.

**ACs:**
- [ ] Migration ensures `core.users (discord_id=0)` and `core.balances (discord_id=0, balance=0)` exist (idempotent INSERT ... ON CONFLICT).
- [ ] Test: `SELECT 1 FROM core.balances WHERE discord_id=0` returns 1 after migration.

**Dependencies:** Luck Story 2.4
**Effort:** S
**Spec refs:** D/W §3.1, §4.6

### Story 2.6 — SECURITY DEFINER deposit fns

**Status:** Done (2026-05-01) — migration `0006_dw_deposit_fns`. Three functions on VPS, EXECUTE granted to `goldrush_dw` only. Smoke-tested locally with full deposit cycle.

**As Aleix I want** `dw.create_deposit_ticket`, `dw.confirm_deposit`, `dw.cancel_deposit` **so that** every deposit-side gold movement is encoded in DB code, not application code.

**ACs:**
- [ ] Three functions created per spec §3.3, owned by `goldrush_admin`, `EXECUTE` granted to `goldrush_dw`.
- [ ] `confirm_deposit` is idempotent on `core.users` insert (`ON CONFLICT DO NOTHING`).
- [ ] `confirm_deposit` writes one `audit_log` row with `action='deposit_confirmed'`, signed amount, balance_before/after.
- [ ] Test: `apply` then `confirm` for a brand-new user creates the user and credits balance correctly.
- [ ] Test: only the cashier who claimed can call `confirm_deposit` (function checks `claimed_by == p_cashier_id`).
- [ ] Test: connecting as `goldrush_dw` and trying `UPDATE core.balances SET balance=...` directly returns permission denied; only EXECUTE on the function works.

**Dependencies:** Story 2.2, Story 2.5
**Effort:** L
**Spec refs:** D/W §3.3

### Story 2.7 — SECURITY DEFINER withdraw fns

**Status:** Done (2026-05-01) — migration `0007_dw_withdraw_fns`. Three functions on VPS. Smoke-tested locally: 30,000 G withdraw with 2 % fee landed 600 G in treasury and persisted `amount_delivered=29,400` on the ticket.

**As Aleix I want** `dw.create_withdraw_ticket`, `dw.confirm_withdraw`, `dw.cancel_withdraw` **so that** withdraw side enforces lock/finalise/refund correctly.

**ACs:**
- [ ] Three functions per spec §3.3.
- [ ] `create_withdraw_ticket` locks balance: validates `balance >= amount`, then `balance -= amount, locked_balance += amount`. Captures `fee = amount * withdraw_fee_bps / 10000` at creation.
- [ ] `confirm_withdraw`: `locked_balance -= amount`; treasury (`core.balances[0].balance += fee`); ticket `amount_delivered = amount - fee, status=confirmed`; audit log row.
- [ ] `cancel_withdraw`: full refund (`balance += amount, locked_balance -= amount`); audit row.
- [ ] Test: lock + confirm: user balance drops by amount, treasury grows by fee.
- [ ] Test: lock + cancel: user balance restored exactly, no orphan in `locked_balance`.
- [ ] Property test: any sequence of (lock, confirm, cancel) for one user keeps `balance >= 0` and `locked_balance >= 0` always.

**Dependencies:** Story 2.2, Story 2.5
**Effort:** L
**Spec refs:** D/W §3.3, §4.2

### Story 2.8 — SECURITY DEFINER lifecycle fns

**Status:** Done (2026-05-01) — migration `0008_dw_lifecycle_fns`. claim_ticket validates region match against `dw.cashier_characters`; release_ticket guarded by claimer identity.

**As Aleix I want** `dw.claim_ticket`, `dw.release_ticket` **so that** assignment is atomic.

**ACs:**
- [ ] `claim_ticket` validates region match against `dw.cashier_characters` for that cashier; raises `region_mismatch` if no compatible char.
- [ ] `claim_ticket` raises `already_claimed` if status != 'open'.
- [ ] `release_ticket` only allows the current `claimed_by` to release.
- [ ] Test: 100 parallel `claim_ticket` calls on same ticket — exactly one succeeds.
- [ ] Test: cashier with only EU char tries to claim NA ticket — raises `region_mismatch`.

**Dependencies:** Story 2.2, Story 2.3
**Effort:** M
**Spec refs:** D/W §3.3, §5.1

### Story 2.9 — SECURITY DEFINER cashier-management fns

**Status:** Done (2026-05-01) — migration `0009_dw_cashier_fns`. add/remove char, set status, all on VPS.

**As Aleix I want** `dw.add_cashier_character`, `dw.remove_cashier_character`, `dw.set_cashier_status` **so that** cashier mgmt is mediated by validated functions.

**ACs:**
- [ ] Three functions per spec §3.3.
- [ ] `add_cashier_character` enforces UNIQUE; duplicate raises `duplicate_character`.
- [ ] `remove_cashier_character` is soft-delete (`is_active=false, removed_at=NOW`).
- [ ] `set_cashier_status` upserts in `dw.cashier_status` and manages `dw.cashier_sessions`: when transitioning to/from `online`, opens/closes a session row.
- [ ] Test: `online → offline` closes the session row with `duration_s` populated.
- [ ] Test: `online → break` closes the online session, opens a break session.

**Dependencies:** Story 2.3
**Effort:** M
**Spec refs:** D/W §3.3, §4.3

### Story 2.10 — SECURITY DEFINER dispute fns

**Status:** Done (2026-05-01) — migration `0010_dw_dispute_fns`. open_dispute (UNIQUE per ticket) + resolve_dispute supporting all four resolution actions.

**ACs:**
- [ ] `dw.open_dispute(ticket_type, ticket_uid, opener_id, opener_role, reason)` per spec.
- [ ] `dw.resolve_dispute(dispute_id, action, amount?, resolved_by)` supports actions: `refund`, `force-confirm`, `partial-refund:<amount>`, `no-action`. Refund actions internally call the relevant `cancel_*` or `treasury_withdraw_to_user` fn.
- [ ] All resolution paths write audit rows.
- [ ] Test: open dispute on a confirmed withdraw → resolve as `refund` → user balance restored, treasury debited.

**Dependencies:** Story 2.4, Story 2.7
**Effort:** L
**Spec refs:** D/W §3.3, §4.5

### Story 2.11 — SECURITY DEFINER treasury fns

**Status:** Done (2026-05-01) — migration `0011_dw_treasury_fns`. treasury_sweep + treasury_withdraw_to_user, both audit-logged with the appropriate row counts (1 for sweep, 2 for transfer).

**As Aleix I want** `dw.treasury_sweep` and `dw.treasury_withdraw_to_user` **so that** every treasury movement is auditable and atomic.

**ACs:**
- [ ] `treasury_sweep(amount, admin_id, reason)`: validates `treasury.balance >= amount`; debits treasury; writes audit row `action='treasury_swept'`. Does not touch any other balance.
- [ ] `treasury_withdraw_to_user(amount, target_user_id, admin_id, reason)`: validates treasury sufficiency; debits treasury; credits user; writes audit row.
- [ ] Test: sweep more than treasury balance → raises `insufficient_treasury`.
- [ ] Test: invariant property — after any sequence of deposits, withdraws, sweeps, refunds, `SUM(user_balances) + treasury_balance + total_swept = total_ever_deposited`.

**Dependencies:** Story 2.5
**Effort:** L
**Spec refs:** D/W §3.3, §4.6

### Story 2.12 — Migration: ban-user fns and `core.users.banned` integration

**Status:** Done (2026-05-01) — migration `0012_dw_ban_fns`. Idempotent user-row creation so admins can pre-emptively ban an unregistered Discord ID.

**ACs:**
- [ ] `dw.ban_user(user_id, reason, admin_id)` flips `core.users.banned=true, banned_reason, banned_at=NOW`; writes audit row.
- [ ] `dw.unban_user(user_id, admin_id)` reverts.
- [ ] Bot's `/deposit` and `/withdraw` commands check `core.users.banned`; reject with ephemeral embed if true.
- [ ] Test: banned user invokes `/deposit` → ephemeral "You are blacklisted" message.

**Dependencies:** Story 2.4
**Effort:** S
**Spec refs:** D/W §3.3, §6.4

---

## EPIC 3 — Core services & models

### Story 3.1 — Balance manager: D/W extensions

**Status:** Done (2026-05-01) — `goldrush_core/db.py`, `goldrush_core/balance/exceptions.py` (32 typed exception classes), `goldrush_core/balance/dw_manager.py` (8 wrappers). 32 unit tests cover every documented sentinel + fallback + specific-match-wins-over-generic ordering. Wrappers: `apply_deposit_ticket`, `confirm_deposit`, `cancel_deposit`, `apply_withdraw_ticket`, `confirm_withdraw`, `cancel_withdraw`, `treasury_sweep`, `treasury_withdraw_to_user`. Cashier / lifecycle / dispute wrappers will land in their own stories (3.x and 9.x).

**ACs:**
- [ ] `goldrush_core/balance/dw_manager.py` exposes typed wrappers around the SECURITY DEFINER fns.
- [ ] Functions: `apply_deposit_ticket`, `confirm_deposit`, `cancel_deposit`, `apply_withdraw_ticket`, `confirm_withdraw`, `cancel_withdraw`, `treasury_sweep`, `treasury_withdraw_to_user`.
- [ ] Each translates Postgres `RaiseError` into typed Python exceptions (`InsufficientBalance`, `RegionMismatch`, `WrongCashier`, `TicketAlreadyClaimed`, `InsufficientTreasury`, `UserBanned`).
- [ ] Test: each exception type triggered by the corresponding DB error.

**Dependencies:** Epic 2 done
**Effort:** M
**Spec refs:** D/W §3.3

### Story 3.2 — Pydantic models for tickets and cashier characters

**Status:** Done (2026-05-01) — `goldrush_core/models/dw_pydantic.py` (16 models: 3 modal-input + 9 domain-row + 4 literal aliases). 66 unit tests cover happy paths, hostile inputs (malformed amount with separators/suffixes/signs/zero, invalid region/faction/charname, oversized realm, boolean amount), normalisation (region case, faction case, hex color), and immutability of frozen models. Domain models constructed from dict payloads matching what asyncpg.Record returns.

**ACs:**
- [ ] `goldrush_core/models/dw_pydantic.py` defines `DepositTicket`, `WithdrawTicket`, `CashierCharacter`, `CashierStatus`, `Dispute`, `DepositModalInput`, `WithdrawModalInput`, `EditDynamicEmbedInput` per spec §5.5.
- [ ] All input models enforce strict validation (region in {EU,NA}, faction in {Alliance,Horde}, charname regex, amount as exact integer, etc.).
- [ ] Test: malformed input raises pydantic ValidationError.

**Dependencies:** Story 2.2, Story 2.3
**Effort:** M
**Spec refs:** D/W §5.5

### Story 3.3 — Embed builders for D/W

Status: Done (2026-05-02)

**ACs:**
- [x] `goldrush_core/embeds/dw_tickets.py` exposes the 14 builders listed in spec §5.6 (deposit ×4, withdraw ×4, cashier_alert, online_cashiers_live, cashier_stats, dispute_open, dispute_resolved, how_to_deposit_dynamic, treasury_balance) plus two helper builders (`awaiting_cashier_embed`, `wait_instructions_embed`) demanded by the visual contract in `reference_deposit_ticket_ux.md`.
- [x] All themed with the GoldRush palette: HOUSE blue `#5B7CC9`, WIN green `#5DBE5A`, BUST red `#D8231A`, EMBER orange `#C8511C`, GOLD `#F2B22A` (Luck §6.3 + visual contract).
- [x] Snapshot-style tests for each embed: title, key fields, colour, timestamp/footer. Anti-phishing warning explicitly covered. Withdraw open shows amount/fee/delivered breakdown; withdraw cancel surfaces `REFUNDED`. Region `NA` is rendered as `(US)` per the visual contract.

**Verification:** `tests/unit/core/test_dw_embeds.py` — 52 tests covering every builder + edge cases (empty cashier roster, null avg-claim-time, zero-amount safety, malformed fields_json fallback, NA→US label, dispute resolved vs rejected colour, etc.). Full unit suite passes 154 / 154.

**Dependencies:** Luck Story 4.10
**Effort:** M
**Spec refs:** D/W §5.6

### Story 3.4 — `/admin setup` channel factory

Status: Done (2026-05-02)

**As Aleix I want** the channel-creation logic isolated and testable **so that** the `/admin setup` command can be exercised in tests without a real Discord guild.

**ACs:**
- [x] `goldrush_deposit_withdraw/setup/channel_factory.py` exposes `setup_or_reuse_channels(guild, *, cashier_role_id, admin_role_id, dry_run=False, persist=None) -> SetupReport`.
- [x] Idempotent: matches by name + parent category. Re-running on a fully provisioned guild creates nothing (verified via `test_second_run_creates_nothing_when_state_unchanged`); partial state creates only the missing entities (`test_partial_state_creates_only_missing_channels`).
- [x] Permission overwrites applied per spec §5.3 matrix. The spec's "View Private Threads" maps to discord.py's `manage_threads` flag (no `view_private_threads` flag exists in discord.py 2.4.0; documented inline). Verified for every role on every channel via dedicated tests for `cashier_alerts`, `disputes`, `how_to_deposit`, `deposit`, the `Cashier` category and the bot member.
- [x] Persistence is decoupled — caller passes an async `persist` callback that receives `{channel_key: discord_id}`; the module never touches the DB. Skipped automatically on `dry_run`.
- [x] `SetupReport` carries per-entity `created` / `reused` flags + `created_count` / `reused_count` properties for the preview embed.
- [x] Tests with in-process discord.py fakes — `_FakeGuild`, `_FakeRole`, `_FakeMember`, `_FakeCategory`, `_FakeChannel`. 23 tests covering: spec sanity, fresh-guild creation, idempotent re-run, partial state, dry-run preview, persistence callback, every permission-matrix branch, role-less degraded mode, frozen-spec immutability.

**Verification:** `tests/unit/dw/test_channel_factory.py` — 23 tests green. Full unit suite 177 / 177; ruff + mypy strict clean on the new module.

**Dependencies:** Story 3.2
**Effort:** L
**Spec refs:** D/W §5.3

---

## EPIC 4 — Bot skeleton

### Story 4.1 — Bot client + healthcheck

Status: Done (2026-05-02)

**ACs:**
- [x] `goldrush_deposit_withdraw/__main__.py` builds the bot via `build_bot(settings)`, configures structlog with the requested format, runs `bot.start(token)` until shutdown, ensures the DB pool closes cleanly on exit.
- [x] `client.py` defines the `DwBot` subclass; `setup_hook` opens the asyncpg pool from `settings.postgres_dsn` (the `goldrush_dw` role DSN injected via Compose) and iterates `EXTENSIONS` to load cogs (the tuple is empty in 4.1; populated in 4.2).
- [x] `healthcheck.py` opens a tiny pool (1 conn, 3-second timeout), runs `SELECT 1`, returns 0 on `result == 1`, 1 on any failure (timeout, exception, missing DSN, wrong return value). Pool closed in a `finally` so the script never leaks sockets.
- [x] Docker HEALTHCHECK already points at `python -m goldrush_deposit_withdraw.healthcheck` (Story 12.4 baseline) — Story 4.1 makes that probe meaningful for the first time.

Companion modules: `goldrush_core/config/__init__.py` (CoreSettings + DwSettings, secrets typed as `SecretStr`), `goldrush_core/logging/__init__.py` (structlog + stdlib logging setup with json/console toggle).

**Verification:** `tests/unit/core/test_settings.py` (9 tests), `tests/unit/dw/test_healthcheck.py` (8 tests), `tests/unit/dw/test_client.py` (8 tests). Full unit suite 201 / 201 green; ruff + mypy strict clean (asyncpg added to mypy `ignore_missing_imports` since the package ships without a py.typed marker).

**Dependencies:** Epic 3 done
**Effort:** M
**Spec refs:** D/W §5.7

### Story 4.2 — Cog loading + per-guild sync

Status: Done (2026-05-02)

**ACs:**
- [x] `EXTENSIONS` populated with the six canonical cog import paths under `goldrush_deposit_withdraw.cogs.*` (account, admin, cashier, deposit, ticket, withdraw). `setup_hook` iterates and `await self.load_extension(ext)` for each. Each cog module exposes the `async def setup(bot)` contract discord.py expects.
- [x] `on_ready` overridden on `DwBot`; calls `bot.tree.sync(guild=discord.Object(id=settings.guild_id))` for instant per-guild sync.
- [x] Logs include user_id + guild_id + synced command_count so a regression in cog loading or command registration is visible at boot.

**Verification:** `tests/unit/dw/test_cogs_loading.py` — 10 tests (1 base + 6 parametrized + 3 functional). 211 / 211 unit tests; ruff + mypy strict clean on the cogs package and the updated client.

**Dependencies:** Story 4.1
**Effort:** S
**Spec refs:** D/W §5.7

### Story 4.3 — Account cog: `/balance` and `/help`

Status: Done (2026-05-02)

**As a user I want** to inspect my balance and ask for help **so that** I do not need to leave Discord.

**ACs:**
- [x] `/balance` runs `fetch_account_stats` (joining `core.users`, `core.balances`, confirmed `dw.deposit_tickets`, confirmed `dw.withdraw_tickets`) and renders `account_summary_embed` with balance + total deposited + total withdrawn + lifetime fees paid. Response is `ephemeral=True`.
- [x] When `fetch_account_stats` returns `None` (no `core.users` row), the cog renders `no_balance_embed` with a deep-link to the `#how-to-deposit` channel (resolved best-effort by name; will read `dw.global_config` once Story 10.x lands).
- [x] `/help` accepts an optional `topic` argument with autocomplete choices (`deposit`, `withdraw`, `fairness`, `support`); without a topic it lists every topic. Unknown topics fall back to the topic list rather than raising.

**Companion code:**
- `goldrush_core/balance/account_stats.py` — frozen `AccountStats` dataclass + async `fetch_account_stats` query.
- `goldrush_core/embeds/account.py` — `account_summary_embed`, `no_balance_embed`, `help_embed`, `HELP_TOPICS` (dict ordered by canonical sequence).

**Verification:** 17 new tests across `tests/unit/core/test_account_stats.py` (4), `tests/unit/core/test_account_embeds.py` (8 incl. parametrized topic test), `tests/unit/dw/test_account_cog.py` (2). Full unit suite 228 / 228 green; ruff + mypy strict clean.

**Dependencies:** Story 4.2, Story 3.1
**Effort:** M
**Spec refs:** D/W §5.1, §5.6

### Story 4.4 — Welcome dynamic embeds (`#how-to-deposit`, `#how-to-withdraw`)

Status: Done (2026-05-02)

**ACs:**
- [x] On startup, `reconcile_welcome_embeds(pool, bot)` runs from `on_ready`. For each managed key (`how_to_deposit`, `how_to_withdraw`) it ensures a `dw.dynamic_embeds` row exists; if absent and a `channel_id` is resolvable (existing row or `dw.global_config.channel_id_<embed_key>`), it INSERTs with the canonical default title + description from `DEFAULT_WELCOMES`.
- [x] When `message_id IS NULL` (fresh row or repost), the reconciler `channel.send(embed=...)` and persists the new id back via UPDATE.
- [x] When `message_id IS NOT NULL`, the reconciler `channel.fetch_message(id)` + `message.edit(embed=...)` — idempotent path that picks up admin edits made via `/admin set-deposit-guide` (Story 10.x).
- [x] Self-heals: a `discord.NotFound` from `fetch_message` (admin deleted the message) triggers a repost and a new id is persisted.
- [x] Skips gracefully when channel id is unknown (pre-`/admin setup`) or when the channel itself can no longer be resolved — no crash, just an info / warning log line.

**Test coverage:** `tests/unit/dw/test_welcome.py` — 11 tests including the "restart twice does not duplicate" property (single message per channel after two reconcile passes), the "stored message deleted → repost" self-heal path, and orchestrator-level skip behaviour for unconfigured channels.

**Verification:** Full unit suite 239 / 239 green; ruff + mypy strict clean. The reconciler is wired into `DwBot.on_ready` with a broad-except wrapper so a DB hiccup never stops the bot from being interactive — the next `on_ready` retries.

**Dependencies:** Story 4.2, Story 3.3
**Effort:** M
**Spec refs:** D/W §5.6

### Story 4.5 — Online cashiers live embed

Status: Done (2026-05-02)

**ACs:**
- [x] `goldrush_core/balance/cashier_roster.py` adds `fetch_online_roster(executor) -> RosterSnapshot` (online cashiers grouped by region, on-break list, offline count). Joins `dw.cashier_status` with `dw.cashier_characters` so a cashier with chars in EU + NA appears in both region buckets.
- [x] `online_cashiers_live_embed` refactored to take a `RosterSnapshot` and render: one field per region (sorted), an "On break" field if non-empty, footer with `Offline cashiers: N`.
- [x] `goldrush_deposit_withdraw/cashiers/live_updater.py` ships `tick(pool, bot, channel_id)` (single iteration with insert / post / edit / repost-on-NotFound branches) plus `OnlineCashiersUpdater` (cancellable asyncio loop, idempotent `start()`, awaitable `stop()`).
- [x] `DwBot.on_ready` resolves the channel id from `dw.global_config.channel_id_online_cashiers` and starts the updater (skipped pre-`/admin setup`); `close_pool` stops the updater on shutdown.
- [x] Test (with two mock cashiers, one EU + one on break NA) renders both in the correct sections; offline count appears in the footer.

**Verification:** 14 new tests in `tests/unit/core/test_cashier_roster.py` (6) and `tests/unit/dw/test_live_updater.py` (7) plus refactor of 3 existing embed tests. Full unit suite 253 / 253 green; ruff + mypy strict clean.

**Dependencies:** Story 4.4
**Effort:** M
**Spec refs:** D/W §5.6, §4.4

---

## EPIC 5 — Deposit flow

### Story 5.1 — `/deposit` command + DepositModal

Status: Done (2026-05-02; paired with 5.2 / 6.1 / 6.2)

**ACs:**
- [x] `/deposit` slash command registered in `goldrush_deposit_withdraw.cogs.deposit.DepositCog`. Channel binding enforced inline (reads `dw.global_config.channel_id_deposit` at invocation time so re-binding via `/admin set-channel` propagates without restart).
- [x] On invocation, opens `DepositModal` (5 fields: char_name, realm, region, faction, amount) — defined in `goldrush_deposit_withdraw/views/modals.py`.
- [x] Modal submit validates via `DepositModalInput` pydantic model (Story 3.2). ValidationError surfaces as an ephemeral list of "field: message" lines so users see exactly what to fix.
- [x] Banned user → `DepositOutcome.UserBanned` → ephemeral "You are blacklisted from creating deposit tickets..." with a pointer to dispute path.
- [x] Rate limit: 1 ticket per user per 60 s, enforced before the thread is created so a tight loop can't litter empty threads.
- [x] On valid input, calls `dw.create_deposit_ticket` via the orchestration helper `open_deposit_ticket` (which translates Postgres RaiseError into typed `DepositOutcome.*` variants).
- [x] Tests for malformed-amount path covered by Story 3.2's `test_dw_pydantic.py`. Out-of-range path covered by `test_ticket_orchestration.py::test_open_deposit_translates_amount_out_of_range`.

**Dependencies:** Story 2.6, Story 4.2
**Effort:** M
**Spec refs:** D/W §4.1, §5.1, §5.5

### Story 5.2 — Deposit thread creation + initial embed

Status: Done (2026-05-02; paired with 5.1 / 6.1 / 6.2)

**ACs:**
- [x] Thread is created BEFORE `dw.create_deposit_ticket` (the SECURITY DEFINER fn requires `thread_id` and `parent_channel_id` as NOT NULL inputs); we name the thread `deposit-pending-<user_id>` initially and rename to the canonical `deposit-{N}` UID after the SECURITY DEFINER returns. `create_ticket_thread` enforces `private_thread`, `invitable=False`, `auto_archive_duration=1440`.
- [x] `thread_id` is passed to the SECURITY DEFINER fn at creation time (the row is inserted with the real id).
- [x] User added via `thread.add_user(interaction.user)` inside `create_ticket_thread`.
- [x] After the row is inserted, bot posts `deposit_ticket_open_embed` in the thread + a literal `@cashier` mention message ("@cashier — new deposit ticket. Run `/claim` to take it.").
- [x] On any failure (banned, range, config, unexpected), the just-created thread is torn down via best-effort delete so the channel doesn't accumulate empty containers; the user gets a friendly ephemeral.
- [x] Test: `test_ticket_factory.py` verifies `private_thread + invitable=False + auto_archive=1440 + user.add` for the factory; `test_deposit_withdraw_cogs.py` verifies the cog registration.

**Dependencies:** Story 5.1, Story 3.3
**Effort:** M
**Spec refs:** D/W §5.4

### Story 5.3 — Cashier alert ping in `#cashier-alerts`

**ACs:**
- [ ] After thread created, bot posts a `cashier_alert_embed` in `#cashier-alerts` mentioning `@cashier`, with thread link.
- [ ] Embed shows: ticket UID, amount, char/realm/region, "compatible cashiers: <list>" if any cashier has matching region char online.
- [ ] Test: with one EU cashier online and an EU ticket, embed lists that cashier as compatible.

**Dependencies:** Story 5.2
**Effort:** S
**Spec refs:** D/W §C.4 (Section C of design)

### Story 5.4 — `/claim`, `/release`, `/cancel` for deposit tickets

**ACs:**
- [ ] `/claim` (in deposit thread): calls `dw.claim_ticket('deposit', uid, user_id)`; on success, edits the open embed to `deposit_ticket_claimed_embed`. Failure cases handled with ephemeral errors (`region_mismatch`, `already_claimed`).
- [ ] `/release` (in deposit thread, claimed by me): calls `dw.release_ticket`; restores `claimed=false` and re-pings cashiers.
- [ ] `/cancel reason:str` (in deposit thread, claimed by me): calls `dw.cancel_deposit`; embeds final cancelled embed; archives thread.
- [ ] `/cancel-mine` (in deposit thread, owned by me): only if status='open' (no claim yet); calls `dw.cancel_deposit`; archives thread.
- [ ] Test: cashier A claims, cashier B `/cancel` fails with `wrong_cashier`.

**Dependencies:** Story 5.2, Story 2.6, Story 2.8
**Effort:** L
**Spec refs:** D/W §4.1

### Story 5.5 — `/confirm` for deposit + 2FA modal

**ACs:**
- [ ] `/confirm` (in deposit thread, claimed by me): opens `ConfirmTicketModal` with magic word "CONFIRM".
- [ ] On submit with mismatched word, ephemeral "Confirmation cancelled".
- [ ] On submit with correct word, calls `dw.confirm_deposit`; on success, posts `deposit_ticket_confirmed_embed` showing the new balance; archives the thread.
- [ ] Updates `cashier_stats` (incremented inside the SECURITY DEFINER fn).
- [ ] Test: typing "confirm" (lowercase) → rejected. Typing "CONFIRM" → accepted.

**Dependencies:** Story 5.4, Story 2.6
**Effort:** M
**Spec refs:** D/W §4.1, §5.5

---

## EPIC 6 — Withdraw flow

### Story 6.1 — `/withdraw` command + WithdrawModal + balance lock

Status: Done (2026-05-02; paired with 5.1 / 5.2 / 6.2)

**ACs:**
- [x] `/withdraw` slash command registered in `WithdrawCog`; channel binding to `#withdraw` enforced inline (`dw.global_config.channel_id_withdraw`).
- [x] `WithdrawModal` defined alongside `DepositModal` in `views/modals.py` with the same 5 fields routed through `WithdrawModalInput`.
- [x] On submit, the orchestration helper `open_withdraw_ticket` calls `dw.create_withdraw_ticket` which locks the balance + captures the fee in one transaction. Insufficient balance surfaces as `WithdrawOutcome.InsufficientBalance` → ephemeral "❌ Insufficient balance: have N, need M".
- [x] Successful submit → `WithdrawOutcome.Success(ticket_uid=...)`; the cog reads the captured fee back from `dw.withdraw_tickets` to render the correct fee in the open embed (so a config change between creation and rendering doesn't cause a mismatch).
- [x] Tests: `test_open_withdraw_translates_insufficient_balance`, `test_open_withdraw_translates_user_not_registered` (a withdraw against a never-registered user yields the typed outcome → ephemeral "make a deposit first").

**Dependencies:** Story 2.7, Story 4.2
**Effort:** M
**Spec refs:** D/W §4.2, §5.1, §5.5

### Story 6.2 — Withdraw thread creation + initial embed (with fee breakdown)

Status: Done (2026-05-02; paired with 5.1 / 5.2 / 6.1)

**ACs:**
- [x] Same private-thread creation as deposit; `withdraw-pending-<user_id>` → renamed to canonical UID after the SECURITY DEFINER returns.
- [x] `withdraw_ticket_open_embed` (Story 3.3) shows `Amount` / `Fee` / `Delivered` upfront — the fee is read back from the inserted row so the rendered value matches what the SECURITY DEFINER captured.
- [x] On failure, the thread is torn down (same `_safe_delete_thread` pattern as deposit).
- [x] Tests: `test_withdraw_open_embed_shows_amount_fee_and_delivered_breakdown` (Story 3.3) covers the visual contract — 50,000 / 1,000 / 49,000 round-trip works for the canonical 2 % fee.

**Dependencies:** Story 6.1, Story 3.3
**Effort:** M
**Spec refs:** D/W §4.2, §5.6

### Story 6.3 — `/claim`, `/release`, `/cancel`, `/cancel-mine` for withdraw

**ACs:**
- [ ] Symmetric to deposit Story 5.4 but with `dw.cancel_withdraw` for cancellation paths (which refunds the locked balance).
- [ ] Test: lock 50K → cancel → balance restored to original; locked_balance back to zero.

**Dependencies:** Story 6.2, Story 2.7
**Effort:** L
**Spec refs:** D/W §4.2

### Story 6.4 — `/confirm` for withdraw + 2FA + treasury credit

**ACs:**
- [ ] Same 2FA modal flow as deposit, but on success calls `dw.confirm_withdraw`: finalises lock as deduction, credits fee to treasury.
- [ ] Final embed shows `withdraw_ticket_confirmed_embed`: "Withdrawn 50,000 G · Received 49,000 G ingame · 1,000 G fee".
- [ ] Test: confirm flow ends with user `balance` reduced by 50K total, `locked_balance` zero, treasury balance increased by 1K.

**Dependencies:** Story 6.3, Story 2.7
**Effort:** M
**Spec refs:** D/W §4.2, §5.5, §5.6

---

## EPIC 7 — Cashier system

### Story 7.1 — `/cashier addchar`, `/cashier removechar`, `/cashier listchars`

**ACs:**
- [ ] `/cashier addchar char realm region faction` validates region/faction; calls `dw.add_cashier_character`; ephemeral confirmation.
- [ ] `/cashier removechar char realm` calls `dw.remove_cashier_character`; ephemeral confirmation.
- [ ] `/cashier listchars` ephemeral embed listing all active chars of the calling cashier.
- [ ] All three commands restricted to `#cashier-onboarding` channel.

**Dependencies:** Story 2.9, Story 4.2
**Effort:** M
**Spec refs:** D/W §5.1

### Story 7.2 — `/cashier set-status` + sessions tracking

**ACs:**
- [ ] `/cashier set-status status:online/offline/break` (any channel) calls `dw.set_cashier_status`.
- [ ] Inserts/closes `dw.cashier_sessions` rows correctly.
- [ ] Triggers refresh of `#online-cashiers` embed.

**Dependencies:** Story 2.9, Story 4.5
**Effort:** S
**Spec refs:** D/W §4.3, §5.1

### Story 7.3 — `/cashier mystats` ephemeral

**ACs:**
- [ ] Reads from `dw.cashier_stats` for the calling user; renders ephemeral embed per spec §6.3 example.
- [ ] If no row exists yet (new cashier), shows zeros.

**Dependencies:** Story 2.9
**Effort:** S
**Spec refs:** D/W §5.1, §6.3

---

## EPIC 8 — Background workers

### Story 8.1 — `ticket_timeout_worker`

**ACs:**
- [ ] Async task runs every 60 s.
- [ ] For each ticket in `dw.deposit_tickets` and `dw.withdraw_tickets` with `status IN ('open','claimed') AND expires_at < NOW()`:
  - If status='open', cancel + (refund if withdraw).
  - If status='claimed', cancel + refund + alert admin in `#alerts` (configurable channel).
- [ ] Each cancellation is via the corresponding SECURITY DEFINER fn (audit-logged).
- [ ] Idempotent: if the worker is killed mid-loop, restarting it correctly cancels remaining tickets.

**Dependencies:** Story 2.6, Story 2.7
**Effort:** M
**Spec refs:** D/W §4.4

### Story 8.2 — `claim_idle_worker`

**ACs:**
- [ ] Runs every 60 s.
- [ ] For tickets `status='claimed' AND last_activity_at < NOW() - 30 min`: auto-release (`dw.release_ticket`) + repost cashier alert.
- [ ] For tickets `status='claimed' AND claimed_at < NOW() - 2h`: auto-cancel + refund (if withdraw) + admin alert.

**Dependencies:** Story 2.8
**Effort:** M
**Spec refs:** D/W §4.4

### Story 8.3 — `cashier_idle_worker`

**ACs:**
- [ ] Runs every 5 min.
- [ ] For each `cashier_status` row with `status='online' AND last_active_at < NOW() - 1h`: auto-set offline; close session with `end_reason='expired'`.

**Dependencies:** Story 2.9
**Effort:** S
**Spec refs:** D/W §4.4

### Story 8.4 — `online_cashiers_embed_updater`

**ACs:**
- [ ] Runs every 30 s.
- [ ] Reads online cashiers from `dw.cashier_status` joined with `dw.cashier_characters`; groups by region.
- [ ] Edits the persisted message in `#online-cashiers` (message_id from `dw.global_config`).
- [ ] If message_id is missing, creates a new message and persists its id.

**Dependencies:** Story 4.5
**Effort:** M
**Spec refs:** D/W §4.4, §5.6

### Story 8.5 — `stats_aggregator`

**ACs:**
- [ ] Runs every 15 min.
- [ ] Recomputes `dw.cashier_stats.avg_claim_to_confirm_s` for cashiers with new confirmations since last run (moving average over last 100 confirmations).
- [ ] Updates `total_online_seconds` from `dw.cashier_sessions`.

**Dependencies:** Story 2.3
**Effort:** M
**Spec refs:** D/W §4.4

### Story 8.6 — `audit_chain_verifier`

**ACs:**
- [ ] Runs every 6 h (or on demand via `/admin verify-audit`).
- [ ] Walks `core.audit_log` from last verified row, recomputes hash chain.
- [ ] On chain break: writes Loki log + sends critical alert via Alertmanager webhook.
- [ ] Stores `last_verified_row_id` in `dw.global_config`.

**Dependencies:** Luck Story 2.5
**Effort:** L
**Spec refs:** D/W §4.4

---

## EPIC 9 — Disputes & blacklist

### Story 9.1 — `/admin dispute open / list / resolve / reject`

**ACs:**
- [ ] `/admin dispute open ticket_uid reason` calls `dw.open_dispute`; posts a `dispute_open_embed` in `#disputes`.
- [ ] `/admin dispute list status?` paginated embed of disputes.
- [ ] `/admin dispute resolve dispute_id action amount?` calls `dw.resolve_dispute`; posts `dispute_resolved_embed`.
- [ ] `/admin dispute reject dispute_id reason` similar but with `status='rejected'`.

**Dependencies:** Story 2.10
**Effort:** L
**Spec refs:** D/W §4.5, §5.1

### Story 9.2 — `#disputes` embed posting

**ACs:**
- [ ] Each dispute open / status change posts a new embed in `#disputes` with status updates editing prior message.
- [ ] Message IDs persisted on the `dw.disputes` row.

**Dependencies:** Story 9.1
**Effort:** S
**Spec refs:** D/W §5.6

### Story 9.3 — `/admin ban-user` and `/admin unban-user`

**ACs:**
- [ ] Both commands restricted to `@admin`; both audit-logged.
- [ ] After ban, banned user's `/deposit` and `/withdraw` invocations rejected with ephemeral "blacklisted" embed.

**Dependencies:** Story 2.12
**Effort:** S
**Spec refs:** D/W §5.1, §6.4

---

## EPIC 10 — Admin commands

### Story 10.1 — `/admin setup` channel auto-creation

**ACs:**
- [ ] Implements spec §5.3 fully.
- [ ] `--dry-run` mode shows preview without creating.
- [ ] Real run creates categories + 8 channels with correct permission overwrites.
- [ ] Persists every channel ID in `dw.global_config`.
- [ ] After channels exist, immediately seeds `dw.dynamic_embeds` for `how_to_deposit` and `how_to_withdraw` and posts them.
- [ ] Test: on a fresh mock guild, dry-run reports "8 channels to create"; real run creates them; second run reports "8 channels reused, 0 created".

**Dependencies:** Story 3.4
**Effort:** L
**Spec refs:** D/W §5.3

### Story 10.2 — `/admin set-deposit-limits`, `/admin set-withdraw-limits`, `/admin set-fee-withdraw`

**ACs:**
- [ ] Three commands updating `dw.global_config` with audit log entries.
- [ ] In-process cache invalidated immediately.

**Dependencies:** Luck Story 4.4 (config caching pattern)
**Effort:** S
**Spec refs:** D/W §5.1

### Story 10.3 — `/admin set-deposit-guide` and `/admin set-withdraw-guide` modals

**ACs:**
- [ ] Open `EditDynamicEmbedModal` with current content prefilled.
- [ ] On submit, updates `dw.dynamic_embeds` row and edits the live Discord message via `message.edit`.
- [ ] Test: editing description updates Discord embed.

**Dependencies:** Story 4.4
**Effort:** M
**Spec refs:** D/W §5.5, §5.6

### Story 10.4 — `/admin promote-cashier`, `/admin demote-cashier`, `/admin force-cashier-offline`

**ACs:**
- [ ] `promote-cashier @user` adds the `@cashier` role to the user (if bot has `Manage Roles` — wait, we don't grant that; alternative: explicit via Discord settings, command is just a reminder). Document this in the command output if Manage Roles is missing.
- [ ] `demote-cashier @user` analogous.
- [ ] `force-cashier-offline @cashier reason` sets status offline + closes session + writes audit row.

**Dependencies:** Story 2.9
**Effort:** M
**Spec refs:** D/W §5.1, §6.5

### Story 10.5 — `/admin cashier-stats @cashier`

**ACs:**
- [ ] Renders the rich stats embed per spec §6.3 example (deposits/withdraws done/cancelled, volume, online time, avg claim→confirm, disputes count, last active).
- [ ] If cashier has no row yet, shows zeros.

**Dependencies:** Story 7.3, Story 2.3
**Effort:** S
**Spec refs:** D/W §6.3

### Story 10.6 — `/admin treasury-balance`, `/admin treasury-sweep`, `/admin treasury-withdraw-to-user`

**ACs:**
- [ ] `treasury-balance` ephemeral shows current treasury balance with note "actual gold lives in the in-game guild bank".
- [ ] `treasury-sweep amount reason` opens 2FA modal expecting "SWEEP" + re-typed amount; on success, calls `dw.treasury_sweep`; webhook alert to `#alerts`.
- [ ] `treasury-withdraw-to-user amount user reason` opens 2FA modal expecting "TREASURY-WITHDRAW" + re-typed amount + re-typed user_id; on success, calls `dw.treasury_withdraw_to_user`; webhook alert.
- [ ] Test: type wrong magic word → operation cancelled with ephemeral message; treasury unchanged.

**Dependencies:** Story 2.11
**Effort:** L
**Spec refs:** D/W §4.6, §5.5, §6.2

### Story 10.7 — `/admin force-cancel-ticket`, `/admin force-close-thread`

**ACs:**
- [ ] `force-cancel-ticket ticket_uid reason` cancels via `dw.cancel_deposit` or `dw.cancel_withdraw` regardless of status (admin override); audited.
- [ ] `force-close-thread thread reason` archives the thread without changing balance — for stuck threads. Audited.

**Dependencies:** Story 2.6, Story 2.7
**Effort:** M
**Spec refs:** D/W §5.1

### Story 10.8 — `/admin view-audit` (shared with Luck)

**ACs:**
- [ ] Same command implementation as Luck §11.4; available in both bots.
- [ ] Filters by `target_id` (user) or returns last N rows.

**Dependencies:** Luck Story 11.4
**Effort:** S
**Spec refs:** D/W §5.1

---

## EPIC 11 — Observability

### Story 11.1 — Prometheus metrics for D/W

**ACs:**
- [ ] `goldrush_deposit_withdraw/metrics.py` defines all metrics from spec §7.3.
- [ ] HTTP server on port 9101 (Luck uses 9100) bound inside `goldrush_net`.
- [ ] Test: scrape `/metrics`, assert all expected metric families present.

**Dependencies:** Luck Story 12.1
**Effort:** M
**Spec refs:** D/W §7.3

### Story 11.2 — Grafana dashboard JSON

**ACs:**
- [ ] `ops/observability/grafana-dashboards/goldrush-dw.json` defines panels: tickets/min by status, volume processed by region, treasury balance over time, cashiers online by region, claim/confirm duration distributions, dispute rate by cashier, fee revenue trend.
- [ ] Imports cleanly into existing Grafana.

**Dependencies:** Story 11.1
**Effort:** L
**Spec refs:** D/W §7.3

### Story 11.3 — Alertmanager rules

**ACs:**
- [ ] Five alerts from spec §7.3 added to `sdr-agentic` Alertmanager config.
- [ ] Webhook to staff `#alerts` Discord channel.
- [ ] Test alert fires on simulated stuck ticket.

**Dependencies:** Story 11.1
**Effort:** S
**Spec refs:** D/W §7.3

---

## EPIC 12 — Operations & deploy

> **Per Aleix's request 2026-05-01: this epic must be EXPLICIT step-by-step. Each story documents the exact shell commands the operator runs. Treat the runbook here as copy-pasteable on the VPS.**

### Story 12.1 — `Dockerfile.dw` (hardened)

**ACs:**
- [ ] `ops/docker/Dockerfile.dw` multi-stage: builder (uv + deps) → runtime (python:3.12-slim, non-root UID 1002, tini PID 1).
- [ ] Image size ≤ 400 MB.
- [ ] Trivy / docker scout scan: no HIGH/CRITICAL.

**Dependencies:** Luck Story 13.1
**Effort:** M
**Spec refs:** D/W §2.3

### Story 12.2 — Compose service `goldrush-deposit-withdraw`

**Status:** Done (2026-05-01)

**ACs:**
- [x] `ops/docker/compose.yml` adds the service per spec §2.3 (Postgres + D/W; Luck service stub left out until Luck resumes).
- [x] No `ports:` mapping (only joins `goldrush_net`).
- [x] Healthcheck configured (the placeholder healthcheck.py exits 0; real DB-aware healthcheck arrives in Epic 4).
- [x] `docker compose up -d` from a clean state succeeds locally — verified during this story (Postgres + schemas/roles/grants instantiated correctly via `00-init-roles.sh` and `01-schemas-grants.sql`).

**Dependencies:** Story 12.1
**Effort:** S
**Spec refs:** D/W §2.3
**Notes:** `init.sql` was split into `00-init-roles.sh` (bash, reads env-var passwords) + `01-schemas-grants.sql` (pure SQL) so Postgres' `/docker-entrypoint-initdb.d/` runs them in the right order with proper variable interpolation. `compose.yml` parameterises `env_file` paths via `${ENV_DIR:-/opt/goldrush/secrets}` so local dev can point elsewhere.

### Story 12.3 — VPS `.env.dw` setup procedure (EXPLICIT)

**Status:** Done (2026-05-01) — actual VPS execution pending

**As Aleix I want** a literal copy-pasteable runbook for setting up the D/W secrets on the VPS **so that** I do not need to remember anything.

**ACs:**
- [ ] `docs/operations.md` has a "D/W bot first-time setup" section with the following commands literally:

```bash
# === EXECUTE AS root ON THE VPS ===

# 1. Confirm prerequisites
ssh sdr-agentic 'whoami && ls -la /opt/goldrush/secrets/'
# expected: root, secrets/ exists with .env.shared

# 2. Create .env.dw with restricted permissions
sudo -u goldrush -- bash -c 'umask 077 && cat > /opt/goldrush/secrets/.env.dw <<EOF
DISCORD_TOKEN_DW=PASTE_YOUR_DW_BOT_TOKEN_HERE
GUILD_ID=PASTE_YOUR_GUILD_ID_HERE
LOG_LEVEL=info
LOG_FORMAT=json
EOF'

# 3. Verify ownership and perms
ls -la /opt/goldrush/secrets/.env.dw
# expected: -rw------- 1 goldrush goldrush ... .env.dw

# 4. Edit the file and replace the placeholders
sudo -u goldrush nano /opt/goldrush/secrets/.env.dw
# replace PASTE_YOUR_DW_BOT_TOKEN_HERE with the token from your local dwBotKeys.txt
# replace PASTE_YOUR_GUILD_ID_HERE with your Discord server ID
# save (Ctrl+O Enter) and exit (Ctrl+X)

# 5. Re-verify perms (some editors break perms on save)
ls -la /opt/goldrush/secrets/.env.dw
# expected: -rw------- 1 goldrush goldrush

# If perms look wrong, fix them:
chmod 600 /opt/goldrush/secrets/.env.dw
chown goldrush:goldrush /opt/goldrush/secrets/.env.dw

# 6. Sanity check (without printing the secret) — confirms placeholders are gone
sudo -u goldrush grep -c 'PASTE_' /opt/goldrush/secrets/.env.dw
# expected: 0
```

- [ ] Document also has a "checklist before first start" section: 1) Discord app created and added to server; 2) Token in `.env.dw`; 3) bot has correct permissions; 4) GUILD_ID set; 5) Postgres healthy.
- [ ] Document includes a "what to do if you mess up the token" recovery: Reset Token in Developer Portal → re-edit `.env.dw` → restart the container.

**Dependencies:** Story 12.2
**Effort:** S
**Spec refs:** D/W §7.2

### Story 12.4 — Initial deployment procedure

**Status:** Done (2026-05-01) — actual VPS execution pending

**As Aleix I want** the literal sequence to first-deploy the D/W bot **so that** I cannot forget a step.

**ACs:**
- [ ] `docs/operations.md` D/W section has a literal command list:

```bash
# === EXECUTE AS goldrush ON THE VPS ===
cd /opt/goldrush/repo

# 1. Pull the latest code
git pull origin main

# 2. Build the D/W image
docker compose -f ops/docker/compose.yml --env-file /opt/goldrush/secrets/.env.shared build goldrush-deposit-withdraw

# 3. Run any pending Alembic migrations (shared with Luck)
docker compose -f ops/docker/compose.yml exec goldrush-luck alembic upgrade head
# (alembic runs as the admin role, applies dw_* migrations)

# 4. Start the D/W service
docker compose -f ops/docker/compose.yml --env-file /opt/goldrush/secrets/.env.shared up -d goldrush-deposit-withdraw

# 5. Tail logs until "ready"
docker compose -f ops/docker/compose.yml logs -f goldrush-deposit-withdraw | grep ready

# 6. In Discord, in the server, run as @admin:
#       /admin setup
#    (preview shows what will be created; click Confirm; ~30 seconds)

# 7. In Server Settings → Integrations → GoldRush Deposit/Withdraw:
#    - For each /admin command: add role override "@admin = Allow"
#    - For each /cashier command: add role override "@cashier = Allow"
#    (this is the per-server visibility config; one-time setup)
```

- [ ] Each step has an "if this fails, do X" sub-bullet for the most likely error.

**Dependencies:** Story 12.3
**Effort:** M
**Spec refs:** D/W §7.2

### Story 12.5 — Subsequent deployment procedure

**Status:** Done (2026-05-01)

**ACs:**
- [ ] `docs/operations.md` D/W section has hot-reload, schema-migration, and rollback procedures (mirror Luck §F.4).
- [ ] Each procedure literal commands.

**Dependencies:** Story 12.4
**Effort:** S
**Spec refs:** D/W §7.6

### Story 12.6 — Backup includes `dw.*` (verification)

**ACs:**
- [ ] Run a manual backup, then restore into a test DB, then verify `dw.*` tables present and populated.
- [ ] Document the verification step in `docs/backup-restore.md`.

**Dependencies:** Luck Story 13.4
**Effort:** S
**Spec refs:** D/W §7.4

---

## EPIC 13 — Documentation final pass

### Story 13.1 — `docs/tickets/` content

**ACs:**
- [ ] Six markdown files written: `deposit-flow.md`, `withdraw-flow.md`, `cashier-onboarding.md`, `ticket-lifecycle.md`, `treasury-management.md`, `disputes.md`, `compliance.md`.
- [ ] Each has worked examples with realistic numbers.
- [ ] `cashier-onboarding.md` is the canonical guide for new cashiers (linked from `#cashier-onboarding`).

**Dependencies:** Most epics done
**Effort:** L
**Spec refs:** D/W §9

### Story 13.2 — ADRs 0011-0017

**ACs:**
- [ ] Seven ADRs written using the standard template (Status, Context, Decision, Consequences, Alternatives), one per major D/W decision per spec §9.
- [ ] All marked `Accepted` with date.

**Dependencies:** ongoing
**Effort:** M (cumulative)
**Spec refs:** D/W §9

### Story 13.3 — Update `security.md`, `runbook.md`, `observability.md`

**ACs:**
- [ ] `docs/security.md` extended with D/W-specific anti-fraud table per spec §6.4.
- [ ] `docs/runbook.md` extended with the six D/W incident playbooks per spec §7.5.
- [ ] `docs/observability.md` extended with the new metrics and alerts.

**Dependencies:** Epics 11, 12 done
**Effort:** M
**Spec refs:** D/W §6, §7

### Story 13.4 — `docs/changelog.md` updated

**ACs:**
- [ ] `dw-v1.0.0` entry written following keep-a-changelog format with all major features listed.
- [ ] Tag `dw-v1.0.0` ready to be applied at launch.

**Dependencies:** All other epics
**Effort:** S
**Spec refs:** D/W §9

---

## EPIC 14 — Testing (cross-epic, finalised here)

### Story 14.1 — Treasury invariant property test

**ACs:**
- [ ] `tests/property/dw/test_treasury_invariant.py`: hypothesis test that runs random sequences of deposit/withdraw/cancel/sweep ops; asserts at every intermediate step that `SUM(user balances) + treasury_balance + total_swept = total_ever_deposited`.
- [ ] Coverage: 1,000 random sequences each with 100 ops; CI passes.

**Dependencies:** Story 2.11
**Effort:** M
**Spec refs:** D/W §8.2

### Story 14.2 — Lifecycle state machine tests

**ACs:**
- [ ] For both deposit and withdraw lifecycles, parameterised test enumerates all (state, action) pairs; valid transitions succeed; invalid transitions raise.
- [ ] Trigger-level test: terminal-state row cannot be modified via direct UPDATE.

**Dependencies:** Story 2.2
**Effort:** M
**Spec refs:** D/W §8.2

### Story 14.3 — Concurrency tests

**ACs:**
- [ ] `tests/integration/dw/test_concurrency.py`:
  - 100 parallel `/withdraw` for one user with insufficient balance — exactly correct number succeed.
  - 10 parallel `/claim` on same ticket — exactly one succeeds.
  - `confirm` racing `force-cancel-ticket` — exactly one wins; never both apply.
  - 100 parallel deposits from 100 distinct new users — exactly 100 `core.users` rows after.

**Dependencies:** Stories 2.6, 2.7, 2.8
**Effort:** L
**Spec refs:** D/W §8.2, §1.3

### Story 14.4 — Cashier permission tests

**ACs:**
- [ ] Only the claimer can `confirm` (test: cashier B tries `confirm` on cashier A's ticket → wrong_cashier).
- [ ] Region mismatch claim refused (test: EU-only cashier claims NA ticket → region_mismatch).
- [ ] Non-cashier (no role) cannot invoke any `/cashier` or `/claim` command (test: ephemeral denial + audit row).

**Dependencies:** Stories 2.8, 2.9
**Effort:** M
**Spec refs:** D/W §8.2

### Story 14.5 — 2FA modal tests

**ACs:**
- [ ] For each magic-word modal (`CONFIRM`, `SWEEP`, `TREASURY-WITHDRAW`): wrong word, missing word, lowercase variant — all rejected; correct word — accepted.
- [ ] Treasury modal: amount mismatch and target mismatch each independently rejected.

**Dependencies:** Stories 5.5, 6.4, 10.6
**Effort:** M
**Spec refs:** D/W §8.2

### Story 14.6 — Worker idempotency tests

**ACs:**
- [ ] For each worker (timeout, claim_idle, cashier_idle, embed updater, stats aggregator, audit verifier): kill mid-execution, restart, verify same end-state as full uninterrupted run.

**Dependencies:** Epic 8
**Effort:** L
**Spec refs:** D/W §8.2

### Story 14.7 — Modal validation tests

**ACs:**
- [ ] DepositModal: rejects amount with separators, regions other than EU/NA, factions other than Alliance/Horde, charname with disallowed chars.
- [ ] WithdrawModal: same plus balance-insufficient case.
- [ ] EditDynamicEmbedModal: rejects malformed JSON in `fields`.

**Dependencies:** Story 3.2
**Effort:** M
**Spec refs:** D/W §8.2

### Story 14.8 — Cross-bot integration tests

**ACs:**
- [ ] Full loop test: deposit 100K → play coinflip on Luck (mocked outcome) lose 50K → withdraw 30K → assert all balance/audit state correct.
- [ ] Permission tests: `goldrush_luck` cannot `INSERT core.users` or `UPDATE core.balances` directly; `goldrush_dw` can.
- [ ] Hash chain integrity test: 100 mixed ops from both bots; chain remains valid; `audit_verify.py` passes.

**Dependencies:** Stories 2.6, 2.7
**Effort:** L
**Spec refs:** D/W §8.4

---

## EPIC 15 — Production verification & launch

### Story 15.1 — End-to-end deposit + withdraw smoke test in real Discord

**ACs:**
- [ ] In a private staging guild, full happy-path exercised: `/admin setup` → cashier registers → user deposits → balance credited → user withdraws → fee in treasury → user receives gold.
- [ ] Manual checklist signed off in `tests/reports/dw-smoke-2026-MM-DD.md`.

**Dependencies:** Epics 5, 6, 7, 10 done
**Effort:** M
**Spec refs:** D/W §1.3

### Story 15.2 — Concurrency stress test in staging

**ACs:**
- [ ] Run 50 simulated users opening deposit and withdraw tickets concurrently; verify no balance drift, no orphan locks, no double-charges, no chain breaks.
- [ ] Report committed.

**Dependencies:** Stories 14.1-14.3
**Effort:** M
**Spec refs:** D/W §1.3

### Story 15.3 — Final security review

**ACs:**
- [ ] `pip-audit` clean.
- [ ] Image scan clean.
- [ ] All SECURITY DEFINER fns reviewed manually for invariants.
- [ ] All audit-log code paths reviewed: every money operation writes a row.
- [ ] Redaction processor verified: test logs contain no token / secret.
- [ ] Dispute resolution audit reviewed: every action writes audit row.
- [ ] Sign-off in `docs/security-review-dw-2026-MM-DD.md`.

**Dependencies:** Epics 1-14 done
**Effort:** L
**Spec refs:** D/W §6

### Story 15.4 — Production deploy + 48h watch

**ACs:**
- [ ] `.env.dw` populated on VPS per Story 12.3.
- [ ] Container started per Story 12.4.
- [ ] `/admin setup` executed in Discord → all channels created.
- [ ] Integrations UI configured for `@admin` and `@cashier` role visibility.
- [ ] Bot online for 48 h without unplanned restart or alert.
- [ ] First real deposit-withdraw cycle completed cleanly in production.
- [ ] `docs/changelog.md` updated to `dw-v1.0.0`.
- [ ] Tag `dw-v1.0.0` pushed to repo.

**Dependencies:** Story 15.3
**Effort:** L
**Spec refs:** D/W §1.3

---

## A. Dependency map (high level)

```
Epic 1 (foundation extensions)
    │
    ▼
Epic 2 (DB schema + SECURITY DEFINER fns)
    │
    ▼
Epic 3 (core services & models)
    │
    ▼
Epic 4 (bot skeleton)
    │
    ├──► Epic 5 (deposit) ──┐
    │                        │
    └──► Epic 6 (withdraw) ──┤
                              │
                              ▼
                          Epic 7 (cashier system)
                              │
                              ▼
                          Epic 8 (background workers)
                              │
                              ▼
                          Epic 9 (disputes)
                              │
                              ▼
                          Epic 10 (admin commands)
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
  Epic 11               Epic 12                 Epic 14
  (observability)       (operations)            (testing)
                              │                       │
                              ▼                       │
                          Epic 13 (docs)              │
                              │                       │
                              └───────────┬───────────┘
                                          ▼
                                     Epic 15 (launch)
```

## B. Estimated cumulative effort

| Epic | Stories | Effort sum |
|---|---|---|
| 1 | 3 | ~1 day |
| 2 | 12 | ~7 days |
| 3 | 4 | ~3 days |
| 4 | 5 | ~3 days |
| 5 | 5 | ~4 days |
| 6 | 4 | ~3 days |
| 7 | 3 | ~2 days |
| 8 | 6 | ~4 days |
| 9 | 3 | ~2 days |
| 10 | 8 | ~5 days |
| 11 | 3 | ~3 days |
| 12 | 6 | ~3 days |
| 13 | 4 | ~3 days |
| 14 | 8 | ~5 days |
| 15 | 4 | ~3 days |
| **Total** | **~78 stories** | **~51 working days** (single dev, no parallelisation) |

Realistic calendar with parallelisation and natural pacing: **~10-12 weeks** from foundation to v1.0.0 production launch, assuming Luck Epics 1-4 are done as prerequisites.

## C. Out of scope explicitly

- Multi-account abuse detection (deferred per project decision).
- Partial-completion of trades.
- VIP / rank-based ticket priority.
- Automated cashier compensation.
- 2-of-N admin signing for treasury withdraws.
- User-initiated disputes.
- Bilingual UX.
- Push-to-prod CI/CD pipeline.
- Multi-region beyond EU/NA.
- Non-retail WoW.

— Aleix, 2026-05-01
