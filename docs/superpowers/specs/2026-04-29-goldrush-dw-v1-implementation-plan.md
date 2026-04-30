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
| **Active phase** | Phase 10 — Operations & deploy (brought forward; Phase 2 still pending) |
| **Active epic** | Epic 12 — Operations & deploy |
| **Active story** | (Epic 1 done; Stories 12.1–12.5 done; Story 12.6 partial; pending VPS execution + Epic 2 onwards) |
| **Last commit** | `b28a605` (Story 1.3) → infrastructure batch commit pending |
| **Next milestone** | Execute VPS setup interactively, deploy stack, then return to Epic 2 (Alembic migrations) |
| **Overall progress** | 8 / 78 stories done · 1 / 15 epics done · Epic 12 in progress (5 / 6) |

### Epic-level status

| Epic | Title | Status | Stories Done |
|---|---|---|---|
| 1 | Foundation extensions | Done | 3 / 3 |
| 2 | Database schema additions | Pending | 0 / 12 |
| 3 | Core services & models | Pending | 0 / 4 |
| 4 | Bot skeleton | Pending | 0 / 5 |
| 5 | Deposit flow | Pending | 0 / 5 |
| 6 | Withdraw flow | Pending | 0 / 4 |
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

**As Aleix I want** the supporting tables in place **so that** disputes, editable embeds, and runtime config are persistable.

**ACs:**
- [ ] Migration creates `dw.disputes`, `dw.dynamic_embeds`, `dw.global_config` per spec §3.2.
- [ ] `dw.global_config` seeded with the 12 default keys from spec §3.2 (min/max amounts, fees, timeouts).
- [ ] Test: re-running the seed is idempotent (no duplicate rows).

**Dependencies:** Story 2.1
**Effort:** S
**Spec refs:** D/W §3.2

### Story 2.5 — Treasury system row

**As Aleix I want** the treasury row to exist after migration **so that** every fee credit has a target.

**ACs:**
- [ ] Migration ensures `core.users (discord_id=0)` and `core.balances (discord_id=0, balance=0)` exist (idempotent INSERT ... ON CONFLICT).
- [ ] Test: `SELECT 1 FROM core.balances WHERE discord_id=0` returns 1 after migration.

**Dependencies:** Luck Story 2.4
**Effort:** S
**Spec refs:** D/W §3.1, §4.6

### Story 2.6 — SECURITY DEFINER deposit fns

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

**ACs:**
- [ ] `dw.open_dispute(ticket_type, ticket_uid, opener_id, opener_role, reason)` per spec.
- [ ] `dw.resolve_dispute(dispute_id, action, amount?, resolved_by)` supports actions: `refund`, `force-confirm`, `partial-refund:<amount>`, `no-action`. Refund actions internally call the relevant `cancel_*` or `treasury_withdraw_to_user` fn.
- [ ] All resolution paths write audit rows.
- [ ] Test: open dispute on a confirmed withdraw → resolve as `refund` → user balance restored, treasury debited.

**Dependencies:** Story 2.4, Story 2.7
**Effort:** L
**Spec refs:** D/W §3.3, §4.5

### Story 2.11 — SECURITY DEFINER treasury fns

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

**ACs:**
- [ ] `goldrush_core/balance/dw_manager.py` exposes typed wrappers around the SECURITY DEFINER fns.
- [ ] Functions: `apply_deposit_ticket`, `confirm_deposit`, `cancel_deposit`, `apply_withdraw_ticket`, `confirm_withdraw`, `cancel_withdraw`, `treasury_sweep`, `treasury_withdraw_to_user`.
- [ ] Each translates Postgres `RaiseError` into typed Python exceptions (`InsufficientBalance`, `RegionMismatch`, `WrongCashier`, `TicketAlreadyClaimed`, `InsufficientTreasury`, `UserBanned`).
- [ ] Test: each exception type triggered by the corresponding DB error.

**Dependencies:** Epic 2 done
**Effort:** M
**Spec refs:** D/W §3.3

### Story 3.2 — Pydantic models for tickets and cashier characters

**ACs:**
- [ ] `goldrush_core/models/dw_pydantic.py` defines `DepositTicket`, `WithdrawTicket`, `CashierCharacter`, `CashierStatus`, `Dispute`, `DepositModalInput`, `WithdrawModalInput`, `EditDynamicEmbedInput` per spec §5.5.
- [ ] All input models enforce strict validation (region in {EU,NA}, faction in {Alliance,Horde}, charname regex, amount as exact integer, etc.).
- [ ] Test: malformed input raises pydantic ValidationError.

**Dependencies:** Story 2.2, Story 2.3
**Effort:** M
**Spec refs:** D/W §5.5

### Story 3.3 — Embed builders for D/W

**ACs:**
- [ ] `goldrush_core/embeds/dw_tickets.py` exposes the 14 builders listed in spec §5.6.
- [ ] All themed with the GoldRush palette (Win/Bust/Gold/Ember/House from Luck §6.3).
- [ ] Snapshot tests for each embed (title, fields, colour, footer).

**Dependencies:** Luck Story 4.10
**Effort:** M
**Spec refs:** D/W §5.6

### Story 3.4 — `/admin setup` channel factory

**As Aleix I want** the channel-creation logic isolated and testable **so that** the `/admin setup` command can be exercised in tests without a real Discord guild.

**ACs:**
- [ ] `goldrush_deposit_withdraw/setup/channel_factory.py` exposes `setup_or_reuse_channels(guild, dry_run=False) -> SetupReport`.
- [ ] Idempotent: if a category or channel already exists by name+parent, reuses it; never creates duplicates.
- [ ] Applies the canonical permission overwrites per spec §5.3 matrix.
- [ ] On real run, persists every channel id into `dw.global_config`.
- [ ] Returns a `SetupReport` with per-channel `created` / `reused` flag for the preview embed.
- [ ] Test (with discord.py mock): on a fresh mock guild, creates 2 categories + 8 channels with correct overwrites.
- [ ] Test: re-running on the same mock state reuses everything; no new entities created.

**Dependencies:** Story 3.2
**Effort:** L
**Spec refs:** D/W §5.3

---

## EPIC 4 — Bot skeleton

### Story 4.1 — Bot client + healthcheck

**ACs:**
- [ ] `goldrush_deposit_withdraw/__main__.py` builds the bot, logs "ready", runs forever.
- [ ] `client.py` defines `Bot` subclass; `setup_hook` connects DB pool with `goldrush_dw` role and loads cogs.
- [ ] `healthcheck.py` opens DB pool, runs `SELECT 1`, exits 0 on success.
- [ ] Docker `HEALTHCHECK` uses this script.

**Dependencies:** Epic 3 done
**Effort:** M
**Spec refs:** D/W §5.7

### Story 4.2 — Cog loading + per-guild sync

**ACs:**
- [ ] Loads all cogs from `EXTENSIONS` constant: `deposit_cog`, `withdraw_cog`, `ticket_cog`, `cashier_cog`, `admin_cog`, `account_cog`.
- [ ] `on_ready` syncs `bot.tree` to `discord.Object(id=GUILD_ID)`.
- [ ] Logs include synced command count.

**Dependencies:** Story 4.1
**Effort:** S
**Spec refs:** D/W §5.7

### Story 4.3 — Account cog: `/balance` and `/help`

**As a user I want** to inspect my balance and ask for help **so that** I do not need to leave Discord.

**ACs:**
- [ ] `/balance` posts ephemeral embed: balance, total deposited (sum of confirmed deposits), total withdrawn, lifetime fee paid.
- [ ] If the user has no `core.users` row → shows the `no_balance_embed` redirecting to `#how-to-deposit`.
- [ ] `/help topic?` lists deposit, withdraw, fairness, support topics.

**Dependencies:** Story 4.2, Story 3.1
**Effort:** M
**Spec refs:** D/W §5.1, §5.6

### Story 4.4 — Welcome dynamic embeds (`#how-to-deposit`, `#how-to-withdraw`)

**ACs:**
- [ ] On startup, bot ensures `dw.dynamic_embeds` has rows for `how_to_deposit` and `how_to_withdraw`; seeds default content if absent.
- [ ] If `message_id IS NULL`, posts the embed in the configured channel and stores the message id.
- [ ] If `message_id IS NOT NULL`, edits the existing message (in case content was updated).
- [ ] Test: deleting the stored message and restarting re-creates it; restarting twice does not duplicate.

**Dependencies:** Story 4.2, Story 3.3
**Effort:** M
**Spec refs:** D/W §5.6

### Story 4.5 — Online cashiers live embed

**ACs:**
- [ ] On startup, bot ensures `#online-cashiers` has the live embed message; creates if absent.
- [ ] Background task `online_cashiers_embed_updater` edits the message every 30 s with current online cashiers grouped by region (EU / NA), plus a "On break" subsection and an "Offline cashiers: N" footer line.
- [ ] Test: with two mock cashiers online (one EU, one NA), the embed renders both in the correct sections.

**Dependencies:** Story 4.4
**Effort:** M
**Spec refs:** D/W §5.6, §4.4

---

## EPIC 5 — Deposit flow

### Story 5.1 — `/deposit` command + DepositModal

**ACs:**
- [ ] Slash command `/deposit` registered, restricted to `#deposit` channel via `@require_channel`.
- [ ] On invocation, opens `DepositModal` with 5 fields per spec §5.5.
- [ ] On submit, pydantic validates input (region, faction, amount range, charname).
- [ ] If user is banned → ephemeral `You are blacklisted` embed; no ticket created.
- [ ] Rate limit applied: max 1 deposit-ticket-creation per user per 60 s.
- [ ] On valid input, calls `dw.create_deposit_ticket` SECURITY DEFINER fn.
- [ ] Test: malformed amount (`"50,000"`) → ValidationError; ephemeral error embed.
- [ ] Test: amount below min → ephemeral "Amount must be ≥ 200 G".
- [ ] Test: amount above max → ephemeral "Amount must be ≤ 200,000 G".

**Dependencies:** Story 2.6, Story 4.2
**Effort:** M
**Spec refs:** D/W §4.1, §5.1, §5.5

### Story 5.2 — Deposit thread creation + initial embed

**ACs:**
- [ ] After `dw.create_deposit_ticket` returns, bot creates a private thread in `#deposit` parent: `name = "deposit-{N}"`, `type = private_thread`, `invitable = false`, `auto_archive_duration = 1440`.
- [ ] Thread ID persisted in the `dw.deposit_tickets.thread_id` column (passed to the create fn).
- [ ] User is added to thread (`thread.add_user(...)`).
- [ ] Bot posts `deposit_ticket_open_embed` in the thread + a message mentioning `@cashier` role to surface the thread.
- [ ] Test: thread created with correct visibility and invited user.

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

**ACs:**
- [ ] Slash command `/withdraw` restricted to `#withdraw` channel.
- [ ] WithdrawModal: same 5 fields as deposit.
- [ ] On submit, pydantic validates; bot fetches user balance; if `balance < amount` → ephemeral "Insufficient balance" with current balance shown.
- [ ] If valid, calls `dw.create_withdraw_ticket`. The fn locks balance (`balance -= amount, locked_balance += amount`) and inserts ticket with `fee` captured.
- [ ] Test: user with 10,000 G balance tries `/withdraw 50000` → ephemeral insufficient + no balance change.
- [ ] Test: user with 100,000 G balance and `/withdraw 50,000` → balance drops to 50,000, locked_balance becomes 50,000, ticket row inserted.

**Dependencies:** Story 2.7, Story 4.2
**Effort:** M
**Spec refs:** D/W §4.2, §5.1, §5.5

### Story 6.2 — Withdraw thread creation + initial embed (with fee breakdown)

**ACs:**
- [ ] Same thread-creation logic as deposit, in `#withdraw` parent.
- [ ] Initial embed shows: amount (gross), fee (2 %), `amount_delivered` = amount − fee (what cashier will trade ingame), char/realm/region/faction.
- [ ] Test: 50,000 G withdraw → embed shows 50,000 / 1,000 fee / 49,000 delivered.

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
