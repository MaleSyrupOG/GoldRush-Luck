# GoldRush Luck v1 — Implementation Plan (Epics, Stories, Acceptance Criteria)

| Field | Value |
|---|---|
| **Document version** | 1.0 |
| **Date** | 2026-04-29 |
| **Author** | Aleix |
| **Repository** | <https://github.com/MaleSyrupOG/GoldRush-Luck> |
| **Status** | Active — drives implementation work |
| **Source of truth for design** | `2026-04-29-goldrush-luck-v1-design.md` (this plan implements that spec) |

---

## 0. How to read this document

The spec is _what_ we are building. This plan is _how_ and _in what order_. It is decomposed into **15 epics**, each containing several **stories**. Every story carries explicit **acceptance criteria** (ACs) — a story is only complete when every AC is verifiably true.

### Conventions

- **Story format:** "As a … I want … so that …" + concrete description + ACs.
- **AC format:** testable, observable assertions (`pytest passes`, `endpoint returns X`, `embed contains Y`). Vague ACs are not allowed.
- **Definition of Done (DoD)** for every story:
  1. Code merged to `main` via PR with passing CI (lint, mypy strict, pip-audit, tests, coverage gates).
  2. All ACs verified.
  3. Relevant docs updated in the same PR (no doc-drift).
  4. Commit message clean — no AI/generator attribution; author is Aleix.
- **Effort sizing:** S (≤ ½ day), M (½–2 days), L (2–5 days), XL (split it).
- **Spec refs:** every story cross-references the relevant section(s) of the design spec.
- **Dependencies:** a story can only start when its dependencies are Done.

### Top-level phase ordering

```
Phase 1 (Foundation)        → Epic 1, 2, 3, 4
Phase 2 (Bot skeleton)      → Epic 5
Phase 3 (Game catalogue)    → Epic 6, 7, 8         (can parallelise after Phase 2)
Phase 4 (Meta-features)     → Epic 9, 10
Phase 5 (Admin & ops)       → Epic 11, 12, 13      (some can overlap with Phase 3-4)
Phase 6 (Docs final pass)   → Epic 14              (incremental throughout, finalised here)
Phase 7 (Launch)            → Epic 15
```

---

## EPIC 1 — Foundation and repo setup

Set up the monorepo, Python toolchain, CI, and documentation skeleton so every subsequent story has a place to land.

### Story 1.1 — Initialise Git repository

**As Aleix I want** a clean Git repo on `main` with the agreed structure **so that** all subsequent work has a versioned home with zero authorship attribution to anything but me.

**ACs:**
- [ ] Local repo initialised at the project root with `main` as default branch.
- [ ] First commit contains: `README.md`, `.gitignore`, `LICENSE`, the empty top-level directories of the monorepo (`goldrush_core/`, `goldrush_luck/`, `goldrush_poker/`, `goldrush_deposit_withdraw/`, `ops/`, `docs/`, `tests/`, `.github/`).
- [ ] Commit message contains no reference to Claude, Anthropic, AI, or auto-generation.
- [ ] `git log --format=%B` for the first commit is reviewed and clean.
- [ ] Remote `origin` set to `https://github.com/MaleSyrupOG/GoldRush-Luck.git`.
- [ ] `main` pushed to remote.
- [ ] `.gitignore` excludes `.env*`, `__pycache__/`, `.venv/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `htmlcov/`, `dist/`, `build/`, `node_modules/`, `.DS_Store`.

**Dependencies:** —
**Effort:** S
**Spec refs:** §2.2

### Story 1.2 — Python toolchain with `uv`

**As Aleix I want** dependency management and packaging fixed at the start **so that** every contributor builds the same artefacts.

**ACs:**
- [ ] `pyproject.toml` configured for Python 3.12, with `uv` as the resolver.
- [ ] Pinned dependencies: `discord.py==2.4.0`, `asyncpg==0.30.0`, `sqlalchemy==2.0.36`, `alembic==1.14.0`, `pydantic==2.10.4`, `pydantic-settings==2.7.0`, `structlog==24.4.0`, `Pillow==11.0.0`, `prometheus-client==0.21.0`.
- [ ] Dev dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis`, `testcontainers[postgres]`, `pip-audit`, `ruff`, `mypy`.
- [ ] `uv.lock` committed.
- [ ] `Makefile` provides targets: `setup`, `lint`, `format`, `type`, `audit`, `test-unit`, `test-integration`, `test-property`, `test`, `run-dev`.
- [ ] `uv sync --frozen` succeeds on a clean clone.

**Dependencies:** Story 1.1
**Effort:** S
**Spec refs:** §2.2, §5.11

### Story 1.3 — CI pipeline

**As Aleix I want** automated checks on every PR **so that** broken or risky code never reaches `main`.

**ACs:**
- [ ] `.github/workflows/ci.yml` runs on `pull_request` to `main` and `push` to `main`.
- [ ] Steps in order: checkout, setup-uv, `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy --strict goldrush_core goldrush_luck`, `pip-audit --strict`, `pytest tests/unit`, `pytest tests/integration` (with Postgres service), `pytest tests/property`, coverage gates per module.
- [ ] Coverage gates enforce `≥ 95 %` on `goldrush_core/balance|fairness|audit`, `≥ 90 %` on `goldrush_core/security` and `goldrush_luck/games/*`, `≥ 85 %` on `goldrush_luck/admin`, `≥ 85 %` global.
- [ ] CI fails if any step fails.
- [ ] Documentation lint job warns (advisory, non-blocking) when code paths change without docs touched.

**Dependencies:** Story 1.2
**Effort:** M
**Spec refs:** §5.11, §8.4

### Story 1.4 — Monorepo skeleton with placeholders

**As Aleix I want** every package to exist as importable modules from day one **so that** PRs add code rather than create directories.

**ACs:**
- [ ] `goldrush_core/{balance,fairness,audit,ratelimit,config,embeds,security,models}/__init__.py` exist.
- [ ] `goldrush_luck/{games,raffle,leaderboard,admin,account,fairness,views}/__init__.py` exist.
- [ ] `goldrush_poker/README.md` and `goldrush_deposit_withdraw/README.md` exist with a one-line "reserved for vN.N".
- [ ] `python -c "import goldrush_core, goldrush_luck"` succeeds.
- [ ] `tests/{unit,integration,property,e2e}/__init__.py` exist.
- [ ] `tests/conftest.py` placeholder created.

**Dependencies:** Story 1.1
**Effort:** S
**Spec refs:** §2.2

### Story 1.5 — Documentation skeleton

**As Aleix I want** `docs/` to mirror its target shape **so that** every PR has the right place to put its doc updates.

**ACs:**
- [ ] `docs/{architecture,security,provably-fair,operations,runbook,backup-restore,observability,dr-plan,release-process,secrets-rotation,responsible-gambling,onboarding,changelog}.md` exist with section outlines + "TBD" markers replaced by inline TODO references to the relevant story.
- [ ] `docs/README.md` lists every doc with a one-line description (the master index).
- [ ] `docs/adr/` exists with `0001-monorepo-layout.md` written.
- [ ] `docs/games/` exists with one stub per game (10 files: `coinflip`, `dice`, `ninetyninex`, `hotcold`, `mines`, `blackjack`, `roulette`, `diceduel`, `stakingduel`, `raffle`).
- [ ] `docs/superpowers/specs/` contains the design spec and this implementation plan.
- [ ] `docs/verifier/` exists with `README.md` placeholder.
- [ ] `docs/api/{bet-lifecycle,deposit-withdraw-flow,fairness-rotation}.md` exist as outlines.

**Dependencies:** Story 1.4
**Effort:** S
**Spec refs:** §9

---

## EPIC 2 — Database foundation

Stand up Postgres with schemas, roles, grants, audit log, and the SECURITY DEFINER economic boundary. Every later epic depends on this being correct.

### Story 2.1 — Postgres compose service

**As Aleix I want** an isolated Postgres container reachable only from `goldrush_net` **so that** no public exposure exists from day one.

**ACs:**
- [ ] `ops/docker/compose.yml` defines `goldrush-postgres` service using `postgres:16-alpine`.
- [ ] Service has no `ports:` mapping (internal only).
- [ ] Volume `goldrush_pgdata` named and mounted at `/var/lib/postgresql/data`.
- [ ] Dedicated bridge network `goldrush_net` declared with subnet `172.30.0.0/24`.
- [ ] Service uses `cap_drop: [ALL]` plus the minimum `cap_add` Postgres needs (CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE).
- [ ] `security_opt: [no-new-privileges:true]` set.
- [ ] Healthcheck via `pg_isready` configured.
- [ ] Manual smoke test: `docker compose up -d postgres && docker compose exec postgres pg_isready` succeeds.

**Dependencies:** Story 1.1
**Effort:** S
**Spec refs:** §3, §7.3

### Story 2.2 — `init.sql` for schemas, roles, grants

**As Aleix I want** the database to be born with the right boundaries **so that** the bot's role can never escalate privileges.

**ACs:**
- [ ] `ops/postgres/init.sql` creates schemas: `core`, `fairness`, `luck`, `poker`.
- [ ] Creates roles: `goldrush_luck`, `goldrush_dw`, `goldrush_poker`, `goldrush_readonly`.
- [ ] Grants per spec §3.1: `goldrush_luck` has SELECT on `core.users` + `core.balances`, INSERT on `core.audit_log`, RW on `fairness.*` and `luck.*`. **No UPDATE/DELETE on `core.balances`**.
- [ ] `goldrush_dw` has INSERT/UPDATE on `core.users` + `core.balances`, INSERT on `core.audit_log`.
- [ ] `goldrush_readonly` has SELECT on all schemas.
- [ ] `ALTER DEFAULT PRIVILEGES` ensures future tables auto-inherit grants.
- [ ] Integration test connects as `goldrush_luck` and proves `UPDATE core.balances` raises `permission denied`.

**Dependencies:** Story 2.1
**Effort:** M
**Spec refs:** §3.1, §3.4

### Story 2.3 — Alembic setup

**As Aleix I want** versioned, reversible migrations **so that** schema evolution is safe.

**ACs:**
- [ ] `ops/alembic/env.py` configured for async asyncpg connection.
- [ ] `alembic.ini` points at `ops/alembic/`.
- [ ] `make migrate-up` and `make migrate-down` defined.
- [ ] Empty initial migration created and applied successfully against the dev container.
- [ ] CI runs `alembic upgrade head` on a fresh test DB and asserts no errors.

**Dependencies:** Story 2.2
**Effort:** S
**Spec refs:** §3.5

### Story 2.4 — `core.users` and `core.balances`

**As Aleix I want** identity and money tables in place with strict invariants **so that** balance integrity is impossible to violate.

**ACs:**
- [ ] Migration creates `core.users` exactly per spec §3.3.
- [ ] Migration creates `core.balances` with `CHECK (balance >= 0)`, `CHECK (locked_balance >= 0)`, `CHECK (total_wagered >= 0)`, `CHECK (total_won >= 0)`.
- [ ] Foreign key `core.balances.discord_id → core.users.discord_id ON DELETE RESTRICT`.
- [ ] Unit test attempts to UPDATE balance to a negative value and asserts Postgres rejects it.
- [ ] SQLAlchemy ORM models in `goldrush_core/models/core.py`.

**Dependencies:** Story 2.3
**Effort:** M
**Spec refs:** §3.3

### Story 2.5 — `core.audit_log` with append-only triggers and hash chain

**As Aleix I want** an immutable, tamper-detectable audit trail **so that** any later compromise is detectable post-hoc.

**ACs:**
- [ ] Migration creates `core.audit_log` per spec §3.3.
- [ ] Triggers `audit_log_no_update` and `audit_log_no_delete` raise on any UPDATE/DELETE.
- [ ] Indexes created: `(target_id, ts DESC)`, `(action, ts DESC)`, `(ref_type, ref_id)`.
- [ ] `prev_hash` and `row_hash` populated by an INSERT-time trigger using `AUDIT_HASH_CHAIN_KEY` (HMAC-SHA256).
- [ ] Integration test: insert N rows, verify `audit_verify.py` reports the chain intact.
- [ ] Integration test: manually edit a row via superuser, verify `audit_verify.py` flags the broken row.
- [ ] Integration test: try `UPDATE core.audit_log SET reason = 'x'` and assert the trigger raises.

**Dependencies:** Story 2.4
**Effort:** L
**Spec refs:** §3.3, §5.6

### Story 2.6 — Fairness schemas

**As Aleix I want** seed storage and history ready before any game is built **so that** `goldrush_core/fairness/` has its DB layer.

**ACs:**
- [ ] Migration creates `fairness.user_seeds` and `fairness.history` per spec §3.3.
- [ ] `fairness.history` has the same append-only triggers as `core.audit_log`.
- [ ] Indexes on `fairness.history (discord_id, rotated_at DESC)`.
- [ ] SQLAlchemy ORM models in `goldrush_core/models/fairness.py`.

**Dependencies:** Story 2.4
**Effort:** S
**Spec refs:** §3.3, §4.2

### Story 2.7 — Luck schemas

**As Aleix I want** all `luck.*` tables created and indexed **so that** game implementations have their persistence layer.

**ACs:**
- [ ] Migration creates: `luck.game_config`, `luck.channel_binding`, `luck.bets`, `luck.bet_rounds`, `luck.game_sessions`, `luck.rate_limit_entries`, `luck.raffle_periods`, `luck.raffle_tickets`, `luck.raffle_draws`, `luck.leaderboard_snapshot`, `luck.global_config` (key/value table for `raffle_rake_bps` etc.).
- [ ] All check constraints and unique constraints from spec §3.3 in place.
- [ ] All indexes from spec §3.3 in place.
- [ ] `luck.bets.idempotency_key` UNIQUE per `discord_id` (composite UNIQUE).
- [ ] Append-only trigger on `luck.raffle_draws`.
- [ ] SQLAlchemy ORM models in `goldrush_core/models/luck.py`.

**Dependencies:** Story 2.6
**Effort:** L
**Spec refs:** §3.3

### Story 2.8 — SECURITY DEFINER economic functions

**As Aleix I want** every gold movement to flow through validated stored procedures **so that** the bot role cannot move money outside the rules.

**ACs:**
- [ ] `luck.apply_bet(...)` implements the full algorithm: lock balance row, validate funds, take house commission, take raffle rake, deduct effective stake, insert into `luck.bets`, write `audit_log` row, update `luck.raffle_periods.pool_amount`. Idempotent on `(discord_id, idempotency_key)`.
- [ ] `luck.resolve_bet(bet_id, status, payout, profit, outcome_jsonb)` releases lock, credits payout, updates `total_wagered` and `total_won`, writes audit row.
- [ ] `luck.refund_bet(bet_id, reason)` returns locked stake, writes audit row.
- [ ] `luck.cashout_mines(bet_id, multiplier)` resolves a Mines bet at a partial multiplier.
- [ ] `luck.consume_rate_token(discord_id, scope, window_s, max_count)` atomic counter.
- [ ] `fairness.rotate_user_seed(discord_id, rotated_by)` moves current seed to history, generates new seed, resets nonce.
- [ ] `fairness.next_nonce(discord_id)` increments and returns.
- [ ] `luck.grant_raffle_tickets(discord_id, bet_amount)` inserts tickets per the threshold.
- [ ] `REVOKE ALL ... FROM PUBLIC; GRANT EXECUTE TO goldrush_luck;` applied to each function.
- [ ] Integration test connects as `goldrush_luck`, calls `apply_bet`, validates correct DB state.
- [ ] Concurrency test: 100 parallel `apply_bet` calls for the same user with limited balance — exact number succeed, balance never < 0.
- [ ] Idempotency test: same key called twice returns the same `bet_id`, no double charge.

**Dependencies:** Story 2.5, Story 2.6, Story 2.7
**Effort:** XL → split into:
  - 2.8a: `apply_bet` + tests
  - 2.8b: `resolve_bet`, `refund_bet`, `cashout_mines` + tests
  - 2.8c: rate limit + nonce + raffle ticket fns + tests
  - 2.8d: `rotate_user_seed` + tests

**Spec refs:** §3.4, §5.1, §5.2

### Story 2.9 — Default `game_config` seed values

**As Aleix I want** the database to ship with the locked v1 economic configuration **so that** the bot is playable immediately after migration.

**ACs:**
- [ ] Migration inserts rows in `luck.game_config` for all nine games with `house_edge_bps = 500`, `min_bet = 100`, `max_bet = 500000`, `enabled = true`.
- [ ] Blackjack `extra_config = {"commission_bps": 450, "rules": "vegas_s17_3to2_noins_nosplit", "decks": 6}`.
- [ ] Roulette `extra_config = {"commission_bps": 236, "variant": "european_single_zero"}`.
- [ ] Mines `extra_config = {"max_mines": 24, "min_mines": 1, "default_mines": 3, "grid_size": 25}`.
- [ ] `luck.global_config` rows: `raffle_rake_bps=100`, `raffle_ticket_threshold_g=100`, `bet_rate_limit_per_60s=30`, `command_rate_limit_per_60s=30`.
- [ ] No row for Flower Poker.
- [ ] Idempotent: re-running the seed script does not duplicate rows.

**Dependencies:** Story 2.7
**Effort:** S
**Spec refs:** §3.3, §10

---

## EPIC 3 — Provably Fair module

Implement the trust core: HMAC engine, seed lifecycle, per-game decoders, and the public verifier.

### Story 3.1 — HMAC-SHA512 engine

**As Aleix I want** a small, audit-friendly module that computes the canonical HMAC **so that** every game shares one source of truth.

**ACs:**
- [ ] `goldrush_core/fairness/engine.py` exports `compute(server_seed: bytes, client_seed: str, nonce: int) -> bytes` returning 64 bytes.
- [ ] Message format is exactly `f"{client_seed}:{nonce}".encode()`.
- [ ] Unit test passes 10 known vectors `(server_seed, client_seed, nonce, expected_hex)`.
- [ ] Property test: same inputs always yield same output (no hidden state).
- [ ] Module imports nothing beyond `hmac` and `hashlib`.

**Dependencies:** Story 1.4
**Effort:** S
**Spec refs:** §4.1

### Story 3.2 — Per-user seed lifecycle

**As Aleix I want** seed state per user with commit/reveal **so that** fairness can be both verified and operationally rotated.

**ACs:**
- [ ] `goldrush_core/fairness/seeds.py` exposes: `ensure_seeds(discord_id)`, `get_public_state(discord_id)`, `set_client_seed(discord_id, new)`, `rotate(discord_id, rotated_by)`.
- [ ] `ensure_seeds` is idempotent: first call creates state with `secrets.token_bytes(32)`, subsequent calls noop.
- [ ] `get_public_state` returns `(server_seed_hash, client_seed, nonce)` — never the raw seed.
- [ ] `set_client_seed` validates input (regex `^[A-Za-z0-9_\-]{1,64}$`); does NOT reset nonce.
- [ ] `rotate` calls `fairness.rotate_user_seed` SECURITY DEFINER fn; returns the revealed previous seed.
- [ ] Test: `repr(SeedState)` and `json.dumps(SeedState)` redact the raw seed.
- [ ] Test: rotating preserves `SHA-256(revealed_seed) == hash_committed_before`.
- [ ] Lint rule (custom or via `ruff` check) blocks any logging statement containing the literal `server_seed` variable name.

**Dependencies:** Story 2.6, Story 2.8d, Story 3.1
**Effort:** M
**Spec refs:** §4.2, §4.3

### Story 3.3 — Per-game decoders

**As Aleix I want** game-specific decoding functions that take HMAC bytes and return outcomes **so that** each game's randomness logic is concentrated and auditable.

**ACs:**
- [ ] `goldrush_core/fairness/decoders.py` exposes one decoder per game, each strictly pure (no I/O, no global state).
- [ ] `decode_coinflip(out: bytes) -> Literal["heads","tails"]` per spec §4.4.
- [ ] `decode_dice(out: bytes) -> float` returns value in `[0.00, 99.99]`.
- [ ] `decode_99x(out: bytes) -> int` returns value in `[1, 100]`.
- [ ] `decode_hotcold(out: bytes) -> Literal["hot","cold","rainbow"]` with documented thresholds.
- [ ] `decode_roulette_eu(out: bytes) -> int` returns value in `[0, 36]`.
- [ ] `decode_mines_positions(out: bytes, mines_count: int, grid_size: int) -> list[int]` returns Fisher-Yates shuffle's first `mines_count` indices.
- [ ] `decode_blackjack_deck(out: bytes, decks: int) -> list[int]` returns shuffled deck of `decks*52` cards.
- [ ] `decode_dice_duel(out: bytes) -> tuple[int, int]` returns (player_roll, bot_roll), each `[1, 12]`.
- [ ] `decode_staking_duel(out: bytes, max_rounds: int) -> list[StakingRound]` pre-computes all rounds.
- [ ] `decode_raffle_winners(out: bytes, ticket_count: int) -> list[int]` returns first 3 indices of Fisher-Yates.
- [ ] Property tests: each decoder's output is always within the documented range across 10,000 random inputs.

**Dependencies:** Story 3.1
**Effort:** L
**Spec refs:** §4.4

### Story 3.4 — Fairness API

**As Aleix I want** a single async API the games call **so that** outcome generation, nonce increment, and audit happen atomically.

**ACs:**
- [ ] `goldrush_core/fairness/api.py` exposes `request_outcome_bytes(discord_id, byte_count, game_context) -> FairnessTicket`.
- [ ] `FairnessTicket` is a frozen pydantic model with `hmac_bytes`, `server_seed_hash`, `client_seed`, `nonce`.
- [ ] Internally calls `fairness.next_nonce` SECURITY DEFINER fn, then `compute()`.
- [ ] Concurrency test: 100 parallel calls for same user → nonces 0..99 each used exactly once, no duplicates, no skips.
- [ ] Returns ticket without exposing raw `server_seed` anywhere in its public surface.

**Dependencies:** Story 3.2, Story 3.3
**Effort:** M
**Spec refs:** §4.6, §4.4

### Story 3.5 — Public verifier (Python and Node.js)

**As Aleix I want** an open verifier that any user can run locally **so that** the casino's fairness claims are auditable by third parties.

**ACs:**
- [ ] `docs/verifier/verify.py` is a single-file zero-dependency script implementing the same algorithm and decoders.
- [ ] `docs/verifier/verify.js` is the equivalent Node.js single-file zero-dependency script.
- [ ] CLI: `python verify.py <game> <server_seed_hex> <client_seed> <nonce> [extra_args]` prints the outcome.
- [ ] Same CLI for `verify.js`.
- [ ] `docs/verifier/EXAMPLES.md` walks through one worked example per game.
- [ ] `docs/verifier/test_vectors.json` ships ≥ 100 known triples with expected outcomes (covers every game).
- [ ] CI cross-check: a test runs both verifiers against `test_vectors.json` and asserts identical results.
- [ ] CI cross-check: a test runs both verifiers against 1,000 random vectors and asserts they match the bot's `goldrush_core/fairness/decoders.py` byte-for-byte.

**Dependencies:** Story 3.3
**Effort:** L
**Spec refs:** §4.5

### Story 3.6 — Force-rotate admin tooling

**As Aleix I want** the ability to force-rotate one user's or all users' seeds **so that** I can respond to leaks immediately.

**ACs:**
- [ ] CLI script `ops/scripts/force_rotate.py` supports `--user <discord_id>` and `--all`.
- [ ] Writes `audit_log` rows for every rotation with `actor_type='admin'`.
- [ ] Idempotent on user level (rotating twice in a row is allowed).
- [ ] Discord-side counterparts (`/admin force-rotate-seed`, `/admin force-rotate-all`) are stub-registered now and wired in Epic 11.

**Dependencies:** Story 3.2
**Effort:** S
**Spec refs:** §4.3, §11

---

## EPIC 4 — Core services

Implement the framework-agnostic primitives that every Discord-layer feature consumes: balance helpers, audit logger, rate limiter, configuration, embeds, security helpers, logging.

### Story 4.1 — Balance manager (Python facade)

**As Aleix I want** a thin Python wrapper around the SECURITY DEFINER fns **so that** game code reads naturally and errors surface as typed exceptions.

**ACs:**
- [ ] `goldrush_core/balance/manager.py` exposes `transactional_bet(...)`, `resolve_bet(...)`, `refund_bet(...)`, `cashout_mines(...)`.
- [ ] Translates Postgres `RaiseError` strings into typed exceptions: `InsufficientBalance`, `UserNotRegistered`, `DuplicateIdempotency`, `GamePaused`, `BetOutOfRange`.
- [ ] `DuplicateIdempotency` is treated as a benign retry: returns the existing bet result.
- [ ] Retries on `SerializationError` with exponential backoff (`0.05 * 2^attempt`, max 3).
- [ ] Test: each exception type is raised on the corresponding DB error.
- [ ] Test: idempotent retry path returns identical result for the same key.

**Dependencies:** Story 2.8
**Effort:** M
**Spec refs:** §5.1, §5.2

### Story 4.2 — Audit logger helper

**As Aleix I want** a single convenience for emitting audit rows **so that** every code path uses consistent fields and never forgets the chain.

**ACs:**
- [ ] `goldrush_core/audit/logger.py` exposes `log(actor_type, actor_id, target_id, action, ...)`.
- [ ] Uses an INSERT into `core.audit_log`; the trigger fills `prev_hash` and `row_hash`.
- [ ] All economic mutations in §4.1 already write audit rows via the SECURITY DEFINER fn — this helper is for non-economic events (admin actions, auth failures, seed rotations, …).
- [ ] Test: `log_failed_authz(member_id, command_name, missing_role)` writes a row with `action='admin_authz_failed'`.

**Dependencies:** Story 2.5
**Effort:** S
**Spec refs:** §5.6

### Story 4.3 — Rate limiter

**As Aleix I want** a uniform rate-limit primitive **so that** every command can throttle abuse.

**ACs:**
- [ ] `goldrush_core/ratelimit/check.py` exposes `check_or_raise(discord_id, scope, max_per_60s)`.
- [ ] Calls `luck.consume_rate_token` SECURITY DEFINER fn.
- [ ] Raises `RateLimited(scope, retry_after_seconds)` when exceeded.
- [ ] Background task in the bot purges entries older than 1 hour.
- [ ] Test: 31st bet within 60 s for the same `(user, scope)` raises `RateLimited`.
- [ ] Test: buckets are isolated per user.

**Dependencies:** Story 2.8c
**Effort:** S
**Spec refs:** §5.6

### Story 4.4 — Game config service with cache

**As Aleix I want** game configuration loaded from DB and cached in memory **so that** every game lookup is fast and consistent across the bot.

**ACs:**
- [ ] `goldrush_core/config/games.py` exposes `get(game_name) -> GameConfig` and `refresh()`.
- [ ] In-memory cache invalidates every 60 s (background task) or on explicit `refresh()`.
- [ ] `GameConfig` is a frozen pydantic model.
- [ ] When admin changes config via `/admin set-bet-limits` etc., the in-process cache is invalidated immediately.
- [ ] Test: changing the row in DB and refreshing yields the new value.

**Dependencies:** Story 2.7
**Effort:** S
**Spec refs:** §3.3, §5.1

### Story 4.5 — Channel binding service with cache

**As Aleix I want** game→channel mapping cached **so that** the channel restriction decorator runs without a DB hit per command.

**ACs:**
- [ ] `goldrush_core/config/channels.py` exposes `get_channel_id(game_name) -> int`.
- [ ] In-memory cache; refresh strategy identical to Story 4.4.
- [ ] Test: cache miss falls through to DB.

**Dependencies:** Story 2.7
**Effort:** S
**Spec refs:** §5.4

### Story 4.6 — Idempotency key generation

**As Aleix I want** a single function that derives the idempotency key per interaction **so that** the convention is enforced.

**ACs:**
- [ ] `goldrush_core/security/idempotency.py` exposes `from_interaction(interaction)` returning `f"discord:{interaction.id}"`.
- [ ] `from_repeat_button(payload)` returns `f"repeat:{payload['orig']}:{payload['ts']}:{payload['u']}"`.
- [ ] Test: deterministic for the same input, distinct for different inputs.

**Dependencies:** Story 1.4
**Effort:** S
**Spec refs:** §5.2

### Story 4.7 — Button signing/verification

**As Aleix I want** every button's `custom_id` HMAC-signed and TTL-bounded **so that** clicks cannot be spoofed or replayed indefinitely.

**ACs:**
- [ ] `goldrush_core/security/buttons.py` exposes `sign_custom_id(payload, ttl=600)` and `verify_custom_id(custom_id) -> dict | None`.
- [ ] Signed format `v1.<base64url(payload+exp)>.<base64url(hmac_sha256(...))>` with 16-char truncated signature (Discord 100-char limit).
- [ ] `verify_custom_id` returns `None` on bad version, bad signature, or expired payload.
- [ ] Test: tampered payload returns None.
- [ ] Test: expired payload returns None.
- [ ] Test: legitimate payload roundtrips correctly.
- [ ] Test: payload's `u` field must equal the clicker's `interaction.user.id` (handler enforces).

**Dependencies:** Story 1.4
**Effort:** M
**Spec refs:** §5.3

### Story 4.8 — Role-check decorator

**As Aleix I want** a decorator that gates commands by role **so that** every admin command is one line away from authz.

**ACs:**
- [ ] `goldrush_core/security/roles.py` exposes `@require_role("admin")`.
- [ ] On failure, sends an ephemeral embed and writes `audit_log` row `action='admin_authz_failed'`.
- [ ] Reads role IDs from a config table or env var; cached.
- [ ] Test: non-admin invocation produces denial + audit row.
- [ ] Test: admin invocation passes through.

**Dependencies:** Story 4.2
**Effort:** S
**Spec refs:** §5.5

### Story 4.9 — Channel-restriction decorator

**As Aleix I want** `@require_channel(game_name)` **so that** game commands only operate in their bound channel.

**ACs:**
- [ ] `goldrush_core/security/channels.py` exposes `@require_channel(game_name)`.
- [ ] On mismatch, sends ephemeral embed redirecting to the correct channel.
- [ ] Reads from Story 4.5 cache.
- [ ] Test: command in wrong channel returns ephemeral redirect, no game side effects.

**Dependencies:** Story 4.5
**Effort:** S
**Spec refs:** §5.4, §6.1

### Story 4.10 — Shared embed builders

**As Aleix I want** a library of themed embed builders **so that** every game's UI is visually consistent and centrally tunable.

**ACs:**
- [ ] `goldrush_core/embeds/colors.py` exports the palette constants from spec §6.3.
- [ ] `goldrush_core/embeds/result.py` builds the canonical bet-result embed with all required fields.
- [ ] `goldrush_core/embeds/errors.py` provides: `no_balance_embed`, `insufficient_balance_embed`, `rate_limited_embed`, `game_paused_embed`, `wrong_channel_embed`, `error_embed(correlation_id)`, `bet_expired_embed`, `bet_out_of_range_embed`.
- [ ] `goldrush_core/embeds/welcome.py` builds welcome embeds for `#fairness` and per-game channels.
- [ ] Snapshot tests verify embed structure (title, fields, colour, footer).

**Dependencies:** Story 1.4
**Effort:** M
**Spec refs:** §6.3, §6.6

### Story 4.11 — Logging with redaction

**As Aleix I want** structured logging in JSON with secret redaction **so that** logs never leak tokens or seeds.

**ACs:**
- [ ] `goldrush_core/logging/setup.py` configures structlog with timestamp, log level, logger name, contextvars, and a `redact_secrets` processor.
- [ ] `redact_secrets` replaces values whose key contains `token|password|secret|server_seed|api_key|dsn` with `***REDACTED***`.
- [ ] Test: logging an event with `server_seed=b'\x01\x02'` produces output without those bytes.
- [ ] Test: logging `password='hunter2'` produces redacted output.
- [ ] `LOG_FORMAT=json` in production, `LOG_FORMAT=text` in dev.

**Dependencies:** Story 1.4
**Effort:** M
**Spec refs:** §5.1, §5.9

### Story 4.12 — Settings loader with `SecretStr`

**As Aleix I want** a single typed settings object loaded from env vars **so that** config errors fail fast at startup.

**ACs:**
- [ ] `goldrush_core/config/settings.py` exposes a `Settings` pydantic-settings class.
- [ ] Required: `DISCORD_TOKEN_LUCK`, `POSTGRES_DSN`, `BUTTON_SIGNING_KEY`, `AUDIT_HASH_CHAIN_KEY`, `GUILD_ID`, `LOG_LEVEL`, `LOG_FORMAT`.
- [ ] All sensitive fields use `SecretStr`.
- [ ] On import, validates length and format (e.g. `DISCORD_TOKEN_LUCK` length > 50, `POSTGRES_DSN.startswith("postgresql://")`).
- [ ] `repr(settings)` and `print(settings)` show `**********` for secrets.
- [ ] Test: missing required var raises at construction.

**Dependencies:** Story 1.4
**Effort:** S
**Spec refs:** §5.7

---

## EPIC 5 — Discord bot skeleton

Bring the bot online, register slash commands, ship the account/fairness cogs.

### Story 5.1 — Bot client and healthcheck

**As Aleix I want** the bot to start, connect, and expose a healthcheck **so that** Docker can monitor it and we can iterate on cogs.

**ACs:**
- [ ] `goldrush_luck/__main__.py` builds the bot, logs "ready", and runs forever.
- [ ] `goldrush_luck/client.py` defines a `Bot` subclass with `setup_hook` that connects DB pool and loads extensions.
- [ ] `goldrush_luck/healthcheck.py` opens a DB pool, runs `SELECT 1`, exits 0 on success / 1 on failure.
- [ ] Docker `HEALTHCHECK` uses this script.
- [ ] Smoke test: `python -m goldrush_luck.healthcheck` exits 0 against a healthy Postgres.

**Dependencies:** Epic 4 done
**Effort:** M
**Spec refs:** §6.5, §7.8

### Story 5.2 — Cog loading and per-guild sync

**As Aleix I want** all cogs auto-loaded and commands synced to one guild **so that** changes appear immediately during dev.

**ACs:**
- [ ] `EXTENSIONS` list loaded from a constant or settings.
- [ ] `on_ready` syncs `bot.tree` to `discord.Object(id=GUILD_ID)` if set.
- [ ] Logs include the synced command count.
- [ ] Manual test: invoking a command in the test guild responds.

**Dependencies:** Story 5.1
**Effort:** S
**Spec refs:** §6.5

### Story 5.3 — Account cog (`/balance`, `/history`, `/help`)

**As a user I want** to see my balance, my recent bets, and a help menu **so that** I can navigate without leaving Discord.

**ACs:**
- [ ] `/balance` returns an ephemeral embed with `balance`, `total_wagered`, `total_won`, `bets_played` (last 30 days), formatted with locale separators (`100,000 G`).
- [ ] If user has no `core.users` row → ephemeral `no_balance_embed`.
- [ ] `/history game?:str limit?:int` (default 10, max 50) returns an embed listing last N bets with `bet_uid`, game, amount, status, profit, ts.
- [ ] `/help topic?:str` lists commands or explains a topic.
- [ ] Tests use a mocked interaction and a real DB (testcontainers).

**Dependencies:** Story 5.2, Story 4.1, Story 4.10
**Effort:** M
**Spec refs:** §6.1

### Story 5.4 — Fairness cog (`/myseed`, `/setseed`, `/rotateseed`, `/fairness`)

**As a user I want** to inspect, change, and rotate my seeds **so that** I control my fairness state.

**ACs:**
- [ ] `/myseed` shows the public state (hash, client_seed, nonce) in an ephemeral embed; never reveals the raw server_seed.
- [ ] `/setseed client_seed:str` validates and updates; ephemeral confirmation.
- [ ] `/rotateseed` opens a confirmation modal "Type ROTATE to confirm"; on submit, calls `rotate(...)`; reveals the previous server_seed in an ephemeral embed.
- [ ] `/fairness` posts a public link to the verifier and to `#fairness`.
- [ ] Tests use a mocked interaction and DB.

**Dependencies:** Story 5.2, Story 3.2, Story 4.10
**Effort:** M
**Spec refs:** §4.2, §6.1

### Story 5.5 — Welcome embeds auto-pin

**As Aleix I want** the bot to ensure pinned welcome embeds exist in `#fairness` and every game channel **so that** new users land on rules and fairness info.

**ACs:**
- [ ] On startup, bot iterates `channel_binding` + the configured `#fairness` channel id.
- [ ] For each, checks pinned messages for the bot's signature; if absent, posts the welcome embed and pins it.
- [ ] If present, no-op.
- [ ] Embeds in English, themed with palette §6.3.
- [ ] Manual test: deleting the pin and restarting the bot re-creates it; restarting twice does not duplicate.

**Dependencies:** Story 5.2, Story 4.10
**Effort:** M
**Spec refs:** §6.6

---

## EPIC 6 — PvE simple games

Implement Coinflip, Dice, 99x, Hot/Cold, Mines via the shared game contract.

### Story 6.1 — Game contract base classes

**As Aleix I want** abstract `Game` and `MultiRoundGame` classes **so that** every game implements the same lifecycle and tests parametrise across all of them.

**ACs:**
- [ ] `goldrush_luck/games/_base.py` defines `Game`, `MultiRoundGame`, `Selection`, `Outcome`, `Resolved`, `SessionState`, `Action`, `ActionResult` per spec §4.6.
- [ ] Pydantic models for type safety.
- [ ] `GAMES_REGISTRY: dict[str, type[Game]]` is the canonical lookup.
- [ ] Adding a new game requires only creating the file + registering.

**Dependencies:** Epic 4 done
**Effort:** M
**Spec refs:** §4.6

### Story 6.2 — Coinflip

**As a user I want** to play `/coinflip bet side` **so that** I can wager on a fair 50/50.

**ACs:**
- [ ] `goldrush_luck/games/coinflip.py` implements `Coinflip(Game)`.
- [ ] `required_bytes = 1`.
- [ ] Decoder: `out[0] & 1 == 0 → heads`.
- [ ] Payout `1.90x` on win, 0 on loss.
- [ ] Embed: gold/red/green colour by status; shows player, won/lost amount, selected, flipped, last 10 flips, fairness footer.
- [ ] Repeat-Bet button signed with HMAC, TTL 600 s.
- [ ] Empirical edge in 100,000 simulated games is `5 % ± 0.5 %`.
- [ ] Channel restriction enforced.
- [ ] Rate limit enforced.

**Dependencies:** Story 6.1, Story 5.2, Story 4.7
**Effort:** M
**Spec refs:** §4.4, §6.1

### Story 6.3 — Dice

**As a user I want** `/dice bet direction threshold` **so that** I can bet over/under on a 1-100 roll.

**ACs:**
- [ ] `Dice(Game)` validates threshold ∈ `[1, 99]` and direction in `{over, under}`.
- [ ] Required bytes = 4.
- [ ] Payout = `(100 / win_size) × 0.95`, where `win_size = 100 - threshold` for over, `threshold` for under.
- [ ] Result embed shows roll value with two decimal places; "Last 10 Rolls" history.
- [ ] Empirical edge in 100,000 simulated games is `5 % ± 0.5 %`.

**Dependencies:** Story 6.1
**Effort:** M
**Spec refs:** §4.4

### Story 6.4 — 99x

**As a user I want** `/99x bet number` **so that** I can chase a 95x payout.

**ACs:**
- [ ] `NinetyNineX(Game)` validates `number ∈ [1, 100]`.
- [ ] Required bytes = 1.
- [ ] Payout `95x` on exact match.
- [ ] Empirical edge `5 % ± 0.5 %`.

**Dependencies:** Story 6.1
**Effort:** S
**Spec refs:** §4.4

### Story 6.5 — Hot/Cold

**As a user I want** `/hotcold bet pick` with `pick ∈ {hot, cold, rainbow}` **so that** I can play the Hot/Cold variant.

**ACs:**
- [ ] `HotCold(Game)` validates pick.
- [ ] Required bytes = 2.
- [ ] Decoding: `n < 500 → rainbow (~5 %)`, `n < 5250 → hot (~47.5 %)`, else cold (~47.5 %).
- [ ] Payout 1.90x for hot/cold, 14.25x for rainbow.
- [ ] Embed shows planted symbol with the rainbow special-cased.
- [ ] Empirical edge `5 % ± 0.5 %` across all three picks.

**Dependencies:** Story 6.1
**Effort:** M
**Spec refs:** §4.4

### Story 6.6 — Mines

**As a user I want** `/mines bet mines_count` **so that** I can reveal tiles, see multipliers grow, and cash out anytime.

**ACs:**
- [ ] `Mines(MultiRoundGame)` validates `mines_count ∈ [1, 24]`.
- [ ] At `apply_bet`, mines positions pre-computed via Fisher-Yates with one nonce, stored in `luck.game_sessions.state`.
- [ ] 5×5 grid of buttons rendered as embed `View`. Tile = closed `❓`, revealed safe `💎`, mine on bust `💣`.
- [ ] Cash-Out button shows live multiplier `combinatorial × 0.95`.
- [ ] Tile reveal action signed and verified per Story 4.7.
- [ ] Cashing out at zero reveals returns the bet (multiplier = 1.0).
- [ ] Hitting a mine sets status `resolved_loss`; remaining mines revealed with `💣`; buttons disabled.
- [ ] Session expires after 10 min of inactivity → automatic cashout at current multiplier.
- [ ] Empirical edge in 100,000 simulated cash-out strategies is `5 % ± 0.5 %`.

**Dependencies:** Story 6.1, Story 4.7
**Effort:** L
**Spec refs:** §4.4, §6.4

---

## EPIC 7 — Casino games

### Story 7.1 — Card asset pipeline

**As Aleix I want** all 52 card faces as PNGs in the design system style **so that** Blackjack renders crisply.

**ACs:**
- [ ] `goldrush_luck/assets/cards/<rank><suit>.png` exists for all 52 cards (e.g. `AS.png`, `TH.png`, `KD.png`).
- [ ] Card backs at `goldrush_luck/assets/cards/back.png`.
- [ ] Style consistent with the GoldRush palette (dark + gold accents).
- [ ] Helper `goldrush_luck/games/blackjack/render.py` composites a hand into a single PNG via Pillow, returns a `discord.File`.

**Dependencies:** Epic 1 done
**Effort:** L
**Spec refs:** §6.4

### Story 7.2 — Blackjack

**As a user I want** `/blackjack bet` with Hit/Stand/Double buttons **so that** I can play Vegas-style blackjack.

**ACs:**
- [ ] `Blackjack(MultiRoundGame)` implements rules: S17, BJ pays 3:2, double-down allowed (any first two cards), no insurance, no split (deferred to v1.1).
- [ ] At `apply_bet`, applies 4.5 % upfront commission per `extra_config.commission_bps` and pre-shuffles `decks*52` cards via Fisher-Yates with one nonce.
- [ ] Buttons: Hit, Stand, Double (only available on first action).
- [ ] Embed shows player hand + dealer up-card; updates per action; final embed reveals dealer hole-card.
- [ ] Bust above 21 → `resolved_loss`.
- [ ] Player BJ vs dealer non-BJ → `resolved_win` 1.5x.
- [ ] Push → `resolved_tie` (refund of effective stake; commission already taken).
- [ ] Session expires after 5 min of inactivity → auto-stand.
- [ ] Empirical edge in 100,000 simulated games (basic strategy): `5 % ± 0.7 %` (slightly looser tolerance because BJ math is rule-coupled).

**Dependencies:** Story 6.1, Story 7.1, Story 4.7
**Effort:** XL → split into:
  - 7.2a: deck dealing + initial hand + BJ detection
  - 7.2b: Hit/Stand actions + dealer play
  - 7.2c: Double-down + commission accounting
  - 7.2d: Card rendering integration + tests

**Spec refs:** §4.4, §6.4

### Story 7.3 — Roulette

**As a user I want** `/roulette bet selection` **so that** I can place every standard EU bet.

**ACs:**
- [ ] `Roulette(Game)` accepts `selection` strings: `"0"`–`"36"`, `"red"`, `"black"`, `"odd"`, `"even"`, `"low"` (1-18), `"high"` (19-36), `"dozen-1"`, `"dozen-2"`, `"dozen-3"`, `"col-1"`, `"col-2"`, `"col-3"`, `"split:n,m"`, `"street:n"` (n=row), `"corner:n,m,p,q"`, `"line:n,m"`.
- [ ] Validates each selection against EU roulette layout.
- [ ] Applies 2.36 % upfront commission per `extra_config.commission_bps`.
- [ ] Payouts standard: straight 35x, split 17x, street 11x, corner 8x, line 5x, dozen/column 2x, even-money 1x.
- [ ] Result embed shows the spun number, colour, and the player's selection result; renders a small wheel image with the result highlighted.
- [ ] Empirical edge in 100,000 simulated games: `5 % ± 0.5 %`.

**Dependencies:** Story 6.1
**Effort:** L
**Spec refs:** §4.4, §6.1

---

## EPIC 8 — Duel games

### Story 8.1 — Dice Duel

**As a user I want** `/diceduel bet` **so that** I can roll against the bot.

**ACs:**
- [ ] `DiceDuel(Game)` rolls two dice 1–12 from one nonce.
- [ ] Higher roll wins; tie consumes the next nonce and rerolls (max 3 re-rolls; 4th tie → refund effective stake).
- [ ] Payout 1.90x on win.
- [ ] Embed shows both rolls and the result.
- [ ] Empirical edge in 100,000 games: `5 % ± 0.5 %`.

**Dependencies:** Story 6.1
**Effort:** M
**Spec refs:** §4.4

### Story 8.2 — Staking Duel

**As a user I want** `/staking bet rounds` **so that** I can watch a multi-round HP-based duel against the bot.

**ACs:**
- [ ] `StakingDuel(Game)` accepts `rounds ∈ [3, 7]`.
- [ ] Players start at 99 HP; each round both inflict damage rolls (1–25) until one HP ≤ 0.
- [ ] All HP/damage rolls pre-computed at `apply_bet` from one nonce.
- [ ] Bot animates the duel by editing the embed every ~1.5 s with the round-by-round state.
- [ ] On tie (both reach 0 HP at the same round) → refund effective stake.
- [ ] Payout 1.90x on win.
- [ ] Empirical edge: `5 % ± 0.5 %`.

**Dependencies:** Story 6.1
**Effort:** L
**Spec refs:** §4.4, §6.4

---

## EPIC 9 — Raffle system

### Story 9.1 — Raffle period management

**As Aleix I want** a service that tracks the active monthly raffle period **so that** tickets and pool know where to land.

**ACs:**
- [ ] `goldrush_luck/raffle/periods.py` exposes `get_active_period()`, `roll_over_if_needed()`.
- [ ] On startup and every hour via background task, `roll_over_if_needed` ensures the current month has an `active` row in `luck.raffle_periods`. If the previous one ended, it's marked `drawing`.
- [ ] Period label format `YYYY-MM`.
- [ ] Idempotent: running twice does not create duplicates.

**Dependencies:** Story 2.7
**Effort:** S
**Spec refs:** §3.3, §10

### Story 9.2 — Ticket granting on bet

**As a user I want** the raffle tickets to credit automatically **so that** I do not need to opt in.

**ACs:**
- [ ] Inside `luck.apply_bet` SECURITY DEFINER fn, after deducting balance, `luck.grant_raffle_tickets(discord_id, bet_amount)` is called.
- [ ] `grant_raffle_tickets` inserts `floor(bet_amount / 100)` rows into `luck.raffle_tickets` linked to the active period and the bet.
- [ ] Test: a 1,000 G bet inserts exactly 10 tickets.
- [ ] Test: a 50 G bet (below threshold, but invalid as min bet is 100) — verify min-bet validation prevents this.
- [ ] Test: a 250 G bet inserts 2 tickets.

**Dependencies:** Story 2.8a, Story 9.1
**Effort:** M
**Spec refs:** §10

### Story 9.3 — `/raffleinfo` and `/raffletickets`

**As a user I want** to inspect the current raffle and my position **so that** I am motivated to play.

**ACs:**
- [ ] `/raffleinfo` posts an embed: pool amount, prizes (50/30/20 % of pool), tickets total, top 10 leaderboard with ticket counts and win-probability percentages, draw date.
- [ ] `/raffletickets` ephemeral: my ticket count, my current win probability, my bets that contributed.
- [ ] Both restricted to `#5b-casino-raffle` (or `#monthly-raffle`).
- [ ] Updates: the public `/raffleinfo` embed in the channel auto-refreshes every 5 min.

**Dependencies:** Story 9.1, Story 9.2, Story 4.10
**Effort:** M
**Spec refs:** §6.1

### Story 9.4 — Monthly draw worker

**As Aleix I want** automatic monthly draws **so that** the raffle runs without manual intervention.

**ACs:**
- [ ] Background task triggers when `now() > active_period.ends_at` and status is `drawing`.
- [ ] Calls `fairness.next_nonce` for a system-level seed (separate `discord_id = 0` system seed account).
- [ ] Pre-computes Fisher-Yates of all tickets; first 3 indices are 1st/2nd/3rd places.
- [ ] Distributes prizes (50/30/20 %) via `core.balances` UPDATE within a SECURITY DEFINER `luck.draw_raffle(period_id)` fn, audit-logged.
- [ ] Inserts `luck.raffle_draws` row with revealed seed and winners.
- [ ] Period status moves to `closed`; new period created.
- [ ] Posts a public announcement embed in the raffle channel and pings winners.

**Dependencies:** Story 9.1, Story 3.4
**Effort:** L
**Spec refs:** §3.3, §10

### Story 9.5 — Raffle audit and verifier integration

**As Aleix I want** the draw outcome to be verifiable by users with the published verifier **so that** raffle results are as trustworthy as game results.

**ACs:**
- [ ] `verify.py` and `verify.js` accept `raffle <revealed_server_seed> <client_seed> <nonce> <ticket_count>` and output the first 3 indices.
- [ ] `EXAMPLES.md` documents how to verify a real past draw.
- [ ] Test vector for a known draw included in `test_vectors.json`.

**Dependencies:** Story 9.4, Story 3.5
**Effort:** S
**Spec refs:** §4.5, §10

---

## EPIC 10 — Leaderboard

### Story 10.1 — Snapshot computation job

**As Aleix I want** the leaderboard pre-computed periodically **so that** queries are O(1) at display time.

**ACs:**
- [ ] Background task every 5 min computes top 10 for each `(period, category)` pair: `period ∈ {daily, weekly, monthly, all_time}`, `category ∈ {top_wagered, top_won, top_big_wins}`.
- [ ] Reads from `luck.bets` and `core.balances`; writes `luck.leaderboard_snapshot` with JSONB payload.
- [ ] Job is non-blocking; failure is logged but does not crash the bot.

**Dependencies:** Story 2.7
**Effort:** M
**Spec refs:** §3.3, §6.1

### Story 10.2 — Leaderboard embed view with auto-refresh

**As a user I want** to see who is on top **so that** I am motivated to climb.

**ACs:**
- [ ] On startup, bot ensures a posted message in `#leaderboard` (created if missing); message ID stored in `luck.global_config`.
- [ ] Edits the message every 5 min with the latest snapshot — three sections (Wagered / Won / Big Wins) each with four time periods.
- [ ] Banner image (`Capa 13.png` style) embedded.
- [ ] Format respects locale (`1,234,567 G`).

**Dependencies:** Story 10.1, Story 4.10
**Effort:** M
**Spec refs:** §6.1, project memory `project_ux_decisions_v1.md`

---

## EPIC 11 — Admin commands

### Story 11.1 — Game configuration commands

**As an admin I want** to tune bet limits and edges live **so that** I can respond to problems without redeploy.

**ACs:**
- [ ] `/admin set-bet-limits game min max` updates `luck.game_config`; audited; cache invalidated.
- [ ] `/admin set-house-edge game bps` updates the same row; rejects bps not in `[0, 10000]`.
- [ ] `/admin view-config` lists all rows in a paginated embed.
- [ ] All registered with `@app_commands.default_permissions()` and `@require_role("admin")`.
- [ ] Test: non-admin invocation hides the command (Discord) or fails with audit row.

**Dependencies:** Story 4.4, Story 4.8
**Effort:** M
**Spec refs:** §6.1, §5.5

### Story 11.2 — Pause/resume commands

**As an admin I want** to pause a game or all games at once **so that** incidents have a containment switch.

**ACs:**
- [ ] `/admin pause-game game` sets `enabled=false`; cache invalidated.
- [ ] `/admin resume-game game` sets `enabled=true`.
- [ ] `/admin pause-all` opens a "Type PAUSE-ALL to confirm" modal; on submit, sets all `enabled=false`.
- [ ] `/admin resume-all` opens equivalent modal.
- [ ] When `enabled=false`, attempting a game command returns the `game_paused_embed`.

**Dependencies:** Story 4.4, Story 4.8
**Effort:** M
**Spec refs:** §6.1

### Story 11.3 — Seed rotation commands

**As an admin I want** to rotate any user's seed (or all seeds) on demand **so that** I can respond to suspected leaks.

**ACs:**
- [ ] `/admin force-rotate-seed user:User` calls `fairness.rotate_user_seed`; ephemeral confirmation.
- [ ] `/admin force-rotate-all` opens "Type ROTATE-ALL to confirm" modal; on submit, iterates all users and rotates each.
- [ ] Both are heavily audited (one row per rotation).

**Dependencies:** Story 4.8, Story 3.6
**Effort:** S
**Spec refs:** §6.1, §4.3

### Story 11.4 — Audit and force-close commands

**As an admin I want** read access to the audit log and the ability to clean up stuck games **so that** I can investigate and recover.

**ACs:**
- [ ] `/admin view-audit user?:User limit:int` posts an embed with paginated rows from `core.audit_log`.
- [ ] `/admin force-close-game bet_uid:str` calls `luck.refund_bet` (refund) or `luck.resolve_bet` based on session state; ephemeral confirmation.

**Dependencies:** Story 4.8, Story 4.1
**Effort:** M
**Spec refs:** §6.1

### Story 11.5 — Channel and rate-limit commands

**As an admin I want** to update channel bindings and rate limits without redeploy **so that** server reorgs do not require ops work.

**ACs:**
- [ ] `/admin set-channel game:str channel:Channel` updates `luck.channel_binding`; cache invalidated.
- [ ] `/admin set-rate-limit scope:str value:int` updates `luck.global_config`.
- [ ] Both audited.

**Dependencies:** Story 4.5, Story 4.8
**Effort:** S
**Spec refs:** §6.1

### Story 11.6 — Modal confirmation primitive

**As an admin I want** consistent magic-word confirmations on dangerous actions **so that** misclicks don't fire them.

**ACs:**
- [ ] `goldrush_core/security/modals.py` exposes `ConfirmDangerousActionModal(label, magic_word, on_confirm)`.
- [ ] Reused by Stories 11.2, 11.3.
- [ ] Test: mismatched word cancels with ephemeral message; correct word fires `on_confirm`.

**Dependencies:** Story 4.8
**Effort:** S
**Spec refs:** §5.5, §6.1

---

## EPIC 12 — Observability

### Story 12.1 — Prometheus metrics endpoint

**As Aleix I want** the bot to expose key metrics **so that** Grafana shows a live operational view.

**ACs:**
- [ ] `goldrush_luck/metrics.py` defines `bets_total`, `bet_amount_g` (histogram), `balance_total_g`, `provably_fair_rotations`, `command_errors_total`, `command_latency_ms` (histogram).
- [ ] Metrics exposed via `prometheus_client` HTTP server on port 9100, bound to `0.0.0.0` inside `goldrush_net`.
- [ ] Test: `curl http://goldrush-luck:9100/metrics` from another container in the network returns the expected lines.

**Dependencies:** Story 5.1
**Effort:** M
**Spec refs:** §7.7

### Story 12.2 — Structured logs to Loki

**As Aleix I want** logs ingested by the existing Loki **so that** queries are unified.

**ACs:**
- [ ] Compose service has `labels: {logging: "promtail", logging_jobname: "goldrush-luck"}`.
- [ ] Verify in Grafana → Loki that `{job="goldrush-luck"}` returns events.
- [ ] Logs include `correlation_id`, `user_id`, `command_name` where applicable.

**Dependencies:** Story 12.1, Story 4.11
**Effort:** S
**Spec refs:** §7.7

### Story 12.3 — Grafana dashboards

**As Aleix I want** a default dashboard **so that** I can monitor at a glance.

**ACs:**
- [ ] `ops/observability/grafana-dashboards/goldrush-luck.json` defines panels: bets per minute (split by game and status), volume wagered (G/h), top-10 active users 24 h, balance distribution histogram, fairness rotations / hour, command error rate, restart events, Postgres connections / locks / slow queries / table sizes.
- [ ] Dashboard imports cleanly into the existing Grafana.

**Dependencies:** Story 12.1
**Effort:** L
**Spec refs:** §7.7

### Story 12.4 — Alertmanager rules

**As Aleix I want** alerts on critical conditions **so that** I am notified before users notice.

**ACs:**
- [ ] Rules: `GoldRushLuckDown`, `GoldRushPostgresDown`, `GoldRushBalanceNegativeAttempt`, `GoldRushHighErrorRate`, `GoldRushUnusualWagering`.
- [ ] Notifications via webhook to a private staff channel `#alerts`.
- [ ] Test alert: trigger a fake `up == 0` and verify webhook delivered.

**Dependencies:** Story 12.1
**Effort:** M
**Spec refs:** §7.7

---

## EPIC 13 — Operations and deploy

### Story 13.1 — `Dockerfile.luck` (hardened)

**ACs:**
- [ ] Multi-stage: builder (uv + deps) → runtime (slim, non-root user UID 1001).
- [ ] `tini` PID 1 to forward signals.
- [ ] Image size ≤ 400 MB.
- [ ] `docker scout` (or `trivy`) shows no HIGH/CRITICAL vulnerabilities.

**Dependencies:** Story 1.2
**Effort:** M
**Spec refs:** §5.1, §7.3

### Story 13.2 — Compose stack

**ACs:**
- [ ] `ops/docker/compose.yml` matches spec §7.3 exactly.
- [ ] `ops/docker/compose.prod.yml` overlay provides cpu/memory limits.
- [ ] Services pass healthcheck within 60 s on a fresh `docker compose up`.

**Dependencies:** Story 13.1, Story 2.1
**Effort:** S
**Spec refs:** §7.3

### Story 13.3 — VPS one-time setup script

**ACs:**
- [ ] `ops/scripts/vps_first_setup.sh` (run as root) creates the `goldrush` user, directory layout (`/opt/goldrush/{repo,secrets,backups,logs,scripts}` with correct perms), generates the GPG key, generates random passwords into `.env.shared`, prints the GPG fingerprint.
- [ ] Script is idempotent: re-running does not regenerate secrets.
- [ ] Documented step-by-step in `docs/operations.md`.

**Dependencies:** Epic 1, 2 done
**Effort:** M
**Spec refs:** §7.2

### Story 13.4 — Backup script + cron

**ACs:**
- [ ] `ops/scripts/backup.sh` performs `pg_dump -Fc | gpg -e` to `/opt/goldrush/backups/daily/`.
- [ ] On day 1 of month, also copies to `monthly/`.
- [ ] Verifies backup size > 1000 bytes and GPG header.
- [ ] Prunes daily older than 30 days, monthly older than 12 months.
- [ ] Optional rsync to Storage Box if SSH key present.
- [ ] `/etc/cron.d/goldrush-backup` runs at 03:00 UTC daily.
- [ ] Restore drill documented in `docs/backup-restore.md`.

**Dependencies:** Story 13.3
**Effort:** M
**Spec refs:** §7.6

### Story 13.5 — Restore procedure + drill

**ACs:**
- [ ] `ops/scripts/restore.sh` accepts a backup file path and restores into a chosen DB name.
- [ ] Documented procedure step-by-step in `docs/backup-restore.md`.
- [ ] Quarterly drill: restore the latest backup into a temporary DB, run `audit_verify.py`, confirm row counts match a fresh dump.

**Dependencies:** Story 13.4
**Effort:** M
**Spec refs:** §7.6

### Story 13.6 — Deploy procedure + runbook

**ACs:**
- [ ] `ops/scripts/deploy.sh` performs `git pull && docker compose up -d --build goldrush-luck` with safety checks (clean working tree, healthy container after).
- [ ] Schema-migration deploy procedure documented in `docs/operations.md`.
- [ ] `docs/runbook.md` covers: bot down, Postgres slow, suspected exploit, seed leak, balance anomaly, Discord token leak, VPS compromise.

**Dependencies:** Story 13.2
**Effort:** M
**Spec refs:** §7.4, §7.9

---

## EPIC 14 — Documentation final pass

Most docs grow incrementally during the prior epics. Here we close the loop and ensure everything is current.

### Story 14.1 — Architecture, security, fairness public docs

**ACs:**
- [ ] `docs/architecture.md` mirrors §1–§3 of the spec but in narrative form, suitable for a new engineer.
- [ ] `docs/security.md` documents the 12 pillars with concrete pointers to code.
- [ ] `docs/provably-fair.md` is user-facing: explains the scheme without crypto jargon, links to the verifier, includes worked examples.
- [ ] All three pass spell-check and a careful read-through.

**Dependencies:** Epics 1-9 mostly complete
**Effort:** L
**Spec refs:** §9

### Story 14.2 — Per-game technical sheets

**ACs:**
- [ ] One markdown file per game in `docs/games/` following the common template (Rules, Payout & Edge, Provably Fair, Configuration, Limits, Edge cases).
- [ ] Internal cross-links to the verifier examples.

**Dependencies:** Epics 6, 7, 8 done
**Effort:** M
**Spec refs:** §9

### Story 14.3 — ADRs

**ACs:**
- [ ] At least 10 ADRs written, covering: monorepo layout, Postgres schemas, per-user PF rotation, no suffixes, channel binding, no-RG-v1, button signing HMAC, SECURITY DEFINER boundary, audit log hash chain, sole authorship, uniform 5 % house edge with commission for BJ/Roulette, Flower Poker exclusion.
- [ ] Each ADR follows the template (Status, Context, Decision, Consequences, Alternatives).

**Dependencies:** ongoing during the project
**Effort:** M (cumulative)
**Spec refs:** §9

### Story 14.4 — Operations / runbook / DR docs

**ACs:**
- [ ] `docs/operations.md`, `docs/runbook.md`, `docs/backup-restore.md`, `docs/observability.md`, `docs/dr-plan.md` complete, each verified against the actual implementation.
- [ ] `docs/release-process.md` documents the manual deploy flow + how to make a tagged release.
- [ ] `docs/secrets-rotation.md` covers each secret's rotation procedure.

**Dependencies:** Epics 12, 13 done
**Effort:** L
**Spec refs:** §9

### Story 14.5 — Onboarding doc

**ACs:**
- [ ] `docs/onboarding.md` is a 30-minute ramp-up guide as outlined in spec §G.8.
- [ ] Tested by reading it end-to-end and ensuring every link resolves.

**Dependencies:** Stories 14.1–14.4
**Effort:** S
**Spec refs:** §9, project memory `project_goldrush_overview.md`

### Story 14.6 — Verifier README and EXAMPLES

**ACs:**
- [ ] `docs/verifier/README.md` explains how to install Node.js or Python, how to run the verifier, what each game's CLI looks like.
- [ ] `docs/verifier/EXAMPLES.md` walks through a verification per game with real seed/nonce values.
- [ ] Both linked from the `#fairness` welcome embed.

**Dependencies:** Story 3.5
**Effort:** S
**Spec refs:** §9, §4.5

---

## EPIC 15 — Production verification and launch

### Story 15.1 — Concurrency stress test

**ACs:**
- [ ] Script runs 100 simultaneous bets per user across 100 simulated users for 5 minutes.
- [ ] No balance ever observed below 0.
- [ ] No double-charge observed in audit log.
- [ ] No deadlock errors or timeouts above 1 % rate.

**Dependencies:** Epic 6 done
**Effort:** M
**Spec refs:** §1.3

### Story 15.2 — Edge simulation per game

**ACs:**
- [ ] For each game, run 100,000 simulated bets at fixed bet size and known strategy.
- [ ] Empirical edge falls within tolerance per the relevant story (5 % ± 0.5 % for parametric games, ± 0.7 % for Blackjack).
- [ ] Results report committed to `tests/reports/edge-simulation-2026-MM-DD.md`.

**Dependencies:** Epic 6, 7, 8 done
**Effort:** M
**Spec refs:** §1.3

### Story 15.3 — End-to-end smoke test in real Discord

**ACs:**
- [ ] In a private staging guild, the bot is invited and configured.
- [ ] Manual checklist exercised: each game playable, fairness verifiable with the published verifier, admin commands hidden from non-admins, channel restriction enforced, leaderboard visible, raffle accumulating tickets.
- [ ] Checklist captured in `tests/reports/smoke-2026-MM-DD.md`.

**Dependencies:** Epics 5–11 done
**Effort:** M
**Spec refs:** §1.3

### Story 15.4 — Final security review

**ACs:**
- [ ] `pip-audit` clean.
- [ ] `docker scout` (or trivy) on the published image: no HIGH/CRITICAL vulnerabilities.
- [ ] Manual review of every `SECURITY DEFINER` function for invariants.
- [ ] Manual review of every audit-log code path: no missed entries.
- [ ] Manual review of redaction processor: no secret leaked in test logs.
- [ ] Sign-off in `docs/security-review-2026-MM-DD.md`.

**Dependencies:** Epics 1-14 done
**Effort:** L
**Spec refs:** §5

### Story 15.5 — Production deploy and 48-hour watch

**ACs:**
- [ ] Deploy procedure executed in production VPS.
- [ ] All admin commands enabled in Discord Integration UI for `@admin`.
- [ ] All channel bindings in place.
- [ ] All welcome embeds pinned.
- [ ] Bot online for 48 hours without unplanned restart.
- [ ] No alert fired in 48 hours that was not benign.
- [ ] First real raffle period rolls over (or scheduled to) without errors.
- [ ] `docs/changelog.md` updated with `luck-v1.0.0` entry.
- [ ] Tag `luck-v1.0.0` pushed to repo.

**Dependencies:** Story 15.4
**Effort:** L
**Spec refs:** §1.3

---

## A. Dependency map (high level)

```
Epic 1 (foundation)
    │
    ▼
Epic 2 (DB foundation)
    │
    ├──► Epic 3 (Provably Fair) ──┐
    │                              │
    └──► Epic 4 (Core services) ──┤
                                   │
                                   ▼
                              Epic 5 (Bot skeleton)
                                   │
       ┌──────┬───────────────────┼───────────────────┐
       ▼      ▼                   ▼                   ▼
  Epic 6  Epic 7              Epic 8              Epic 11
  (PvE)   (Casino)            (Duels)             (Admin)
       │      │                   │                   │
       └──────┴───────────────────┼───────────────────┘
                                   │
                                   ▼
                              Epic 9 (Raffle), Epic 10 (Leaderboard)
                                   │
                                   ▼
                              Epic 12 (Observability)
                                   │
                                   ▼
                              Epic 13 (Operations)
                                   │
                                   ▼
                              Epic 14 (Docs final)
                                   │
                                   ▼
                              Epic 15 (Launch)
```

## B. Estimated cumulative effort

| Epic | Stories | Effort sum |
|---|---|---|
| 1 | 5 | ~3 days |
| 2 | 9 (with sub-stories) | ~6 days |
| 3 | 6 | ~4 days |
| 4 | 12 | ~5 days |
| 5 | 5 | ~3 days |
| 6 | 6 | ~5 days |
| 7 | 3 (BJ split into sub-stories) | ~6 days |
| 8 | 2 | ~2 days |
| 9 | 5 | ~4 days |
| 10 | 2 | ~2 days |
| 11 | 6 | ~3 days |
| 12 | 4 | ~3 days |
| 13 | 6 | ~4 days |
| 14 | 6 | ~4 days |
| 15 | 5 | ~3 days |
| **Total** | **~82 stories** | **~57 working days** (single dev, no parallelisation) |

Realistic calendar with parallelisable epics and natural pacing: **~3 months** to v1.0.0 production launch.

## C. Out of scope explicitly

- Flower Poker game (per collaborator decision 2026-04-29).
- Card splitting in Blackjack (deferred to v1.1).
- Bilingual UX (English-only in v1).
- Self-exclusion / responsible gambling features (deferred; documented in `docs/responsible-gambling.md`).
- Automated push-to-prod CI/CD pipeline (manual deploy only in v1).
- Cross-server multi-guild support (single guild in v1).
- The Poker bot and the Deposit/Withdraw bot — separate specs and plans.

— Aleix, 2026-04-29
