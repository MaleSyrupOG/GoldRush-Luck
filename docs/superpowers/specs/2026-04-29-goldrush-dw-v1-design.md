# GoldRush Deposit/Withdraw v1 — Design Specification

| Field | Value |
|---|---|
| **Document version** | 1.0 |
| **Date** | 2026-04-29 |
| **Author** | Aleix |
| **Repository** | <https://github.com/MaleSyrupOG/GoldRush-Luck> (monorepo) |
| **Status** | Locked — ready for implementation planning |
| **Companion specs** | `2026-04-29-goldrush-luck-v1-design.md` (sister bot, currently paused) |

---

## 0. Executive summary

GoldRush Deposit/Withdraw (D/W) is the **economic frontier** of the GoldRush platform. It is the only system component permitted to create users in `core.users`, credit balances during deposits, debit balances during withdrawals, and move gold between players' accounts and the operator-controlled treasury. Every gold movement that is not the result of a game outcome flows through this bot.

The bot mediates between two worlds:

1. **The Discord world**, where users hold a numeric `core.balances.balance` they can spend in the Luck bot's games.
2. **The World of Warcraft world**, where the actual gold lives in a guild bank controlled by the operator (Aleix). Real value is moved by **cashiers** — trusted humans with the `@cashier` role — who execute trades inside the game and confirm them through the bot.

The bot ships with full deposit and withdraw flows, a private-thread-based ticket system, cashier online/offline tracking, automated timeouts and refunds, dispute management, treasury accounting, and an `/admin setup` command that bootstraps the entire channel structure on first install.

The design priorities, in order, are: **balance integrity** (no balance ever becomes negative; no gold ever materialises out of nothing or vanishes silently), **operational verifiability** (every cashier action is auditable and tied to a Discord identity with 2FA-style confirmation), **isolation from the games bot** (a compromise of Luck cannot mint gold; a compromise of D/W cannot manipulate game outcomes).

## 1. Goals and non-goals

### 1.1. Goals (v1)

1. `/deposit` flow: user → modal → private thread → cashier `/claim` → in-game trade → cashier `/confirm` → `core.users` row created if needed + balance credited.
2. `/withdraw` flow: user → modal → balance locked → private thread → cashier `/claim` → in-game trade of `amount − fee` to user → cashier `/confirm` → fee credited to treasury.
3. Stateless WoW-identity capture: every modal asks character, realm, region, faction, amount; nothing is persisted on the user side.
4. Cashier lifecycle: registration of WoW characters, online/offline/break status, FCFS ticket claim, audited cancellation, accountability stats.
5. Private-thread tickets in dedicated parent channels; thread auto-archives on resolution; permission model isolates user privacy while allowing cashiers to claim.
6. Background workers for ticket timeouts (24h open, 30m claim idle, 2h claim total), cashier inactivity, online-cashiers embed refresh, audit chain verification.
7. Dispute system for admin investigation and resolution of contested tickets.
8. Treasury accounting: fees accumulate in `core.balances[discord_id=0]`; admins periodically physically retrieve gold from the in-game guild bank and reconcile via `/admin treasury-sweep`.
9. `/admin setup` command that auto-creates the canonical channel structure with correct per-role permissions on first install.
10. Editable `#how-to-deposit` and `#how-to-withdraw` welcome embeds via admin commands.
11. Full security parity with Luck (12 pillars), with extra defences appropriate to the economic frontier role.
12. Integration with the existing observability stack (Loki, Prometheus, Grafana, Alertmanager) using the `goldrush_dw` job label.
13. Documentation in `docs/` covering architecture, security, ticket flows, runbook, ADRs, and operational guides.

### 1.2. Non-goals (v1)

1. **Multi-account abuse detection** — explicitly deferred (per project decision); documented as known gap.
2. **Card splitting / exotic deposit flows** — straight gold deposits only.
3. **Multi-currency** — only `G` (WoW gold). No tokens, no real money, no fiat.
4. **Multi-region beyond EU/NA** — Asia, OCE, LATAM not supported in v1.
5. **Non-retail WoW** — no Classic, no SoD, no HC, no Cata Classic.
6. **Cashier compensation automation** — operator pays cashiers off-platform; the bot only tracks stats.
7. **VIP / rank-based ticket priority** — FIFO only in v1.
8. **Partial-completion of trades** — full or nothing; partial gold delivered → cancel + dispute.
9. **2-of-N admin signing for treasury withdraws** — single-admin 2FA modal in v1; multi-admin signing deferred.
10. **User-initiated disputes** — admins open all disputes in v1; users must contact an admin.
11. **Bilingual UX** — English-only.
12. **Push-to-prod CI/CD** — manual deploy from VPS only.

### 1.3. Success criteria

- All happy paths exercisable end-to-end: deposit → balance credited; withdraw → fee in treasury, gold reaches user.
- All cancel paths refund correctly; no orphaned `locked_balance` ever.
- 100 simultaneous deposit and withdraw operations from distinct users complete without deadlock or race-induced corruption.
- Property test confirms invariant `SUM(user balances) + treasury_balance + admin_swept_total` equals total ever deposited, across thousands of randomised operations.
- `/admin setup` produces the full channel structure idempotently on a fresh test guild.
- The bot's DB role `goldrush_dw` cannot mint gold beyond the SECURITY DEFINER functions: integration test attempts `UPDATE core.balances SET balance = ...` and asserts permission denied.
- Audit-log hash chain remains intact across all D/W operations; `audit_verify.py` passes after a full smoke-test run.

---

## 2. System architecture

### 2.1. Placement in the monorepo

D/W is a sibling Python package alongside Luck. Both consume `goldrush_core` for shared logic (balance, audit, ratelimit, embeds, security, models).

```
goldrush/
├── goldrush_core/                     # shared (already specified in Luck design)
│   ├── balance/                       # + helpers for deposit/withdraw flows
│   ├── audit/
│   ├── ratelimit/
│   ├── config/                        # + dw runtime config keys
│   ├── embeds/                        # + builders for ticket states
│   ├── security/
│   └── models/                        # + SQLAlchemy models for dw tables
│
├── goldrush_luck/                     # sister bot (paused)
├── goldrush_deposit_withdraw/         # ← this bot
│   ├── __main__.py
│   ├── client.py
│   ├── healthcheck.py
│   ├── tickets/
│   │   ├── deposit.py
│   │   ├── withdraw.py
│   │   ├── lifecycle.py               # state machine
│   │   └── timeouts.py                # background timeout workers
│   ├── cashiers/
│   │   ├── characters.py
│   │   ├── status.py
│   │   ├── stats.py
│   │   └── alerts.py
│   ├── commands/
│   │   ├── deposit_cog.py
│   │   ├── withdraw_cog.py
│   │   ├── ticket_cog.py
│   │   ├── cashier_cog.py
│   │   ├── admin_cog.py
│   │   └── account_cog.py
│   ├── views/                         # modals, buttons
│   └── setup/                         # /admin setup channel auto-creation
│       └── channel_factory.py
└── goldrush_poker/                    # placeholder
```

### 2.2. Process and permission isolation

| Concern | Luck | D/W |
|---|---|---|
| Discord application | `GoldRush Luck` | `GoldRush Deposit/Withdraw` |
| Discord token | `DISCORD_TOKEN_LUCK` | `DISCORD_TOKEN_DW` |
| Container UID | 1001 | 1002 |
| Container name | `goldrush-luck` | `goldrush-dw` |
| DB role | `goldrush_luck` | `goldrush_dw` |
| `core.users` access | SELECT only | INSERT, UPDATE |
| `core.balances` access | SELECT (writes only via SECURITY DEFINER fns) | SELECT, UPDATE (only via SECURITY DEFINER fns) |
| `dw.*` access | none | SELECT, INSERT, UPDATE |
| `luck.*` access | RW | none |
| `fairness.*` access | RW | none |
| Required Discord permissions | Send Messages, Embed Links, Use Slash Commands, Manage Threads (within parent), Read Message History, Add Reactions, Use External Emojis | Same **plus Manage Channels** (for `/admin setup`) |
| Privileged intents | none | none |

A compromise of one bot does not give arbitrary access to the other's domain. The DB enforces this via per-role grants. The OS enforces it via UIDs and read-only filesystems.

### 2.3. Compose stack additions

```yaml
services:
  goldrush-deposit-withdraw:
    build:
      context: ../..
      dockerfile: ops/docker/Dockerfile.dw
    image: goldrush-dw:latest
    container_name: goldrush-dw
    restart: unless-stopped
    networks: [goldrush_net]
    env_file:
      - /opt/goldrush/secrets/.env.shared
      - /opt/goldrush/secrets/.env.dw
    environment:
      POSTGRES_DSN: postgresql://goldrush_dw:${PG_DW_PASSWORD}@postgres:5432/goldrush
    user: "1002:1002"
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    pids_limit: 256
    mem_limit: 384m
    mem_reservation: 192m
    healthcheck:
      test: ["CMD", "python", "-m", "goldrush_deposit_withdraw.healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "5"}
    labels:
      logging: "promtail"
      logging_jobname: "goldrush-dw"
    depends_on:
      postgres:
        condition: service_healthy
```

---

## 3. Database schema additions

The full set of `core.*` tables (`users`, `balances`, `audit_log` with hash chain) is already defined in the Luck spec §3. D/W adds a new schema `dw`.

### 3.1. Schema and grants

```sql
CREATE SCHEMA dw;

GRANT USAGE ON SCHEMA dw TO goldrush_dw;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA dw TO goldrush_dw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA dw TO goldrush_dw;
ALTER DEFAULT PRIVILEGES IN SCHEMA dw
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_dw;
ALTER DEFAULT PRIVILEGES IN SCHEMA dw
    GRANT USAGE, SELECT ON SEQUENCES TO goldrush_dw;

-- read-only role gets SELECT for Grafana / debugging
GRANT USAGE ON SCHEMA dw TO goldrush_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA dw TO goldrush_readonly;

-- D/W gets writes on core.users and core.balances
GRANT INSERT, UPDATE ON core.users TO goldrush_dw;
GRANT INSERT, UPDATE ON core.balances TO goldrush_dw;
GRANT INSERT ON core.audit_log TO goldrush_dw;

-- Luck cannot create users
REVOKE INSERT ON core.users FROM goldrush_luck;
REVOKE UPDATE ON core.balances FROM goldrush_luck;  -- enforced for clarity, even if the original GRANT never included it
```

The system row that represents the operator-controlled treasury:

```sql
INSERT INTO core.users (discord_id, created_at) VALUES (0, NOW())
    ON CONFLICT DO NOTHING;
INSERT INTO core.balances (discord_id, balance) VALUES (0, 0)
    ON CONFLICT DO NOTHING;
```

`discord_id = 0` is reserved as the treasury account.

### 3.2. Tables

#### `dw.deposit_tickets`

```sql
CREATE TABLE dw.deposit_tickets (
    id                  BIGSERIAL   PRIMARY KEY,
    ticket_uid          TEXT        NOT NULL UNIQUE,
    discord_id          BIGINT      NOT NULL,                        -- no FK: user may not exist yet
    char_name           TEXT        NOT NULL,
    realm               TEXT        NOT NULL,
    region              TEXT        NOT NULL CHECK (region IN ('EU','NA')),
    faction             TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
    amount              BIGINT      NOT NULL CHECK (amount > 0),
    status              TEXT        NOT NULL CHECK (status IN ('open','claimed','confirmed','cancelled','expired','disputed')),
    claimed_by          BIGINT,
    claimed_at          TIMESTAMPTZ,
    confirmed_at        TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    cancel_reason       TEXT,
    thread_id           BIGINT      NOT NULL UNIQUE,
    parent_channel_id   BIGINT      NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deposit_status_created  ON dw.deposit_tickets (status, created_at DESC);
CREATE INDEX idx_deposit_user_created    ON dw.deposit_tickets (discord_id, created_at DESC);
CREATE INDEX idx_deposit_cashier_claimed ON dw.deposit_tickets (claimed_by, claimed_at DESC);
CREATE INDEX idx_deposit_open_expires    ON dw.deposit_tickets (expires_at) WHERE status IN ('open','claimed');

-- once a ticket reaches a terminal state, it cannot transition out of it
CREATE OR REPLACE FUNCTION dw.deposit_ticket_terminal_immutable() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('confirmed','cancelled','expired')
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        RAISE EXCEPTION 'cannot change status from terminal % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER deposit_ticket_terminal BEFORE UPDATE ON dw.deposit_tickets
    FOR EACH ROW EXECUTE FUNCTION dw.deposit_ticket_terminal_immutable();
```

#### `dw.withdraw_tickets`

Symmetric to deposit but with FK to `core.users` (the user must exist to withdraw) and explicit `fee` and `amount_delivered` columns.

```sql
CREATE TABLE dw.withdraw_tickets (
    id                  BIGSERIAL   PRIMARY KEY,
    ticket_uid          TEXT        NOT NULL UNIQUE,
    discord_id          BIGINT      NOT NULL REFERENCES core.users(discord_id) ON DELETE RESTRICT,
    char_name           TEXT        NOT NULL,
    realm               TEXT        NOT NULL,
    region              TEXT        NOT NULL CHECK (region IN ('EU','NA')),
    faction             TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
    amount              BIGINT      NOT NULL CHECK (amount > 0),    -- gross
    fee                 BIGINT      NOT NULL CHECK (fee >= 0),       -- captured at creation
    amount_delivered    BIGINT,                                       -- = amount - fee, set at confirm
    status              TEXT        NOT NULL CHECK (status IN ('open','claimed','confirmed','cancelled','expired','disputed')),
    claimed_by          BIGINT,
    claimed_at          TIMESTAMPTZ,
    confirmed_at        TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    cancel_reason       TEXT,
    thread_id           BIGINT      NOT NULL UNIQUE,
    parent_channel_id   BIGINT      NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_withdraw_status_created  ON dw.withdraw_tickets (status, created_at DESC);
CREATE INDEX idx_withdraw_user_created    ON dw.withdraw_tickets (discord_id, created_at DESC);
CREATE INDEX idx_withdraw_cashier_claimed ON dw.withdraw_tickets (claimed_by, claimed_at DESC);
CREATE INDEX idx_withdraw_open_expires    ON dw.withdraw_tickets (expires_at) WHERE status IN ('open','claimed');

-- analogous terminal-status trigger
```

#### `dw.cashier_characters`

```sql
CREATE TABLE dw.cashier_characters (
    id              BIGSERIAL   PRIMARY KEY,
    discord_id      BIGINT      NOT NULL,
    char_name       TEXT        NOT NULL,
    realm           TEXT        NOT NULL,
    region          TEXT        NOT NULL CHECK (region IN ('EU','NA')),
    faction         TEXT        NOT NULL CHECK (faction IN ('Alliance','Horde')),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at      TIMESTAMPTZ,
    UNIQUE (discord_id, char_name, realm, region)
);
CREATE INDEX idx_cashier_chars_active    ON dw.cashier_characters (discord_id) WHERE is_active = TRUE;
CREATE INDEX idx_cashier_chars_by_region ON dw.cashier_characters (region) WHERE is_active = TRUE;
```

#### `dw.cashier_status` and `dw.cashier_sessions`

```sql
CREATE TABLE dw.cashier_status (
    discord_id      BIGINT      PRIMARY KEY,
    status          TEXT        NOT NULL CHECK (status IN ('online','offline','break')),
    set_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    auto_offline_at TIMESTAMPTZ,
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cashier_status_online ON dw.cashier_status (set_at DESC) WHERE status = 'online';

CREATE TABLE dw.cashier_sessions (
    id              BIGSERIAL   PRIMARY KEY,
    discord_id      BIGINT      NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    duration_s      BIGINT,
    end_reason      TEXT        CHECK (end_reason IN ('manual_offline','manual_break','auto_disconnect','admin_force','expired'))
);
CREATE INDEX idx_cashier_sessions_user_started ON dw.cashier_sessions (discord_id, started_at DESC);
```

#### `dw.cashier_stats`

```sql
CREATE TABLE dw.cashier_stats (
    discord_id              BIGINT      PRIMARY KEY,
    deposits_completed      BIGINT      NOT NULL DEFAULT 0,
    deposits_cancelled      BIGINT      NOT NULL DEFAULT 0,
    withdraws_completed     BIGINT      NOT NULL DEFAULT 0,
    withdraws_cancelled     BIGINT      NOT NULL DEFAULT 0,
    total_volume_g          BIGINT      NOT NULL DEFAULT 0,
    total_online_seconds    BIGINT      NOT NULL DEFAULT 0,
    avg_claim_to_confirm_s  INTEGER,
    last_active_at          TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `dw.disputes`

```sql
CREATE TABLE dw.disputes (
    id                  BIGSERIAL   PRIMARY KEY,
    ticket_type         TEXT        NOT NULL CHECK (ticket_type IN ('deposit','withdraw')),
    ticket_uid          TEXT        NOT NULL,
    opener_id           BIGINT      NOT NULL,
    opener_role         TEXT        NOT NULL CHECK (opener_role IN ('admin','user','system')),
    reason              TEXT        NOT NULL,
    status              TEXT        NOT NULL CHECK (status IN ('open','investigating','resolved','rejected')),
    resolution          TEXT,
    resolved_by         BIGINT,
    resolved_at         TIMESTAMPTZ,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticket_type, ticket_uid)
);
CREATE INDEX idx_disputes_status ON dw.disputes (status, opened_at DESC);
```

#### `dw.dynamic_embeds`

Editable welcome embeds (e.g. `#how-to-deposit` content) stored here so admins can edit content without redeploy.

```sql
CREATE TABLE dw.dynamic_embeds (
    embed_key       TEXT        PRIMARY KEY,
    channel_id      BIGINT      NOT NULL,
    message_id      BIGINT,
    title           TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    color_hex       TEXT        NOT NULL DEFAULT '#F2B22A',
    fields          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    image_url       TEXT,
    footer_text     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      BIGINT      NOT NULL
);
```

#### `dw.global_config`

```sql
CREATE TABLE dw.global_config (
    key             TEXT        PRIMARY KEY,
    value_int       BIGINT,
    value_text      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      BIGINT      NOT NULL
);

INSERT INTO dw.global_config (key, value_int, updated_by) VALUES
    ('min_deposit_g',            200,    0),
    ('max_deposit_g',            200000, 0),
    ('min_withdraw_g',           1000,   0),
    ('max_withdraw_g',           200000, 0),
    ('withdraw_fee_bps',         200,    0),                          -- 2 %
    ('deposit_fee_bps',          0,      0),
    ('daily_user_limit_g',       0,      0),
    ('ticket_expiry_open_s',     86400,  0),
    ('ticket_repinging_s',       3600,   0),
    ('ticket_claim_idle_s',      1800,   0),
    ('ticket_claim_expiry_s',    7200,   0),
    ('cashier_auto_offline_s',   3600,   0);
```

### 3.3. SECURITY DEFINER functions — the economic frontier

All money-moving paths go through `SECURITY DEFINER` functions owned by `goldrush_admin`. The bot's `goldrush_dw` role only has `EXECUTE` on these.

| Function | Purpose | Atomic side effects |
|---|---|---|
| `dw.create_deposit_ticket` | Open a deposit ticket | INSERT `deposit_tickets`; INSERT `audit_log` (`deposit_ticket_opened`). No balance change. |
| `dw.confirm_deposit` | Cashier confirms in-game trade received | INSERT `core.users` (idempotent); UPDATE `core.balances` (+ amount); UPDATE ticket status; INSERT `audit_log` (`deposit_confirmed`); UPDATE `cashier_stats`. |
| `dw.cancel_deposit` | Close ticket without crediting | UPDATE ticket status to `cancelled`; INSERT `audit_log`. No balance change. |
| `dw.create_withdraw_ticket` | Open withdraw ticket and lock balance | UPDATE `core.balances` (`balance -= amount`, `locked_balance += amount`); INSERT `withdraw_tickets`; INSERT `audit_log` (`withdraw_locked`). |
| `dw.confirm_withdraw` | Cashier confirms in-game gold delivered | UPDATE `core.balances` (`locked_balance -= amount`); UPDATE treasury (`balance += fee`); UPDATE ticket; INSERT `audit_log` (`withdraw_confirmed`); UPDATE `cashier_stats`. |
| `dw.cancel_withdraw` | Refund locked stake | UPDATE `core.balances` (`balance += amount`, `locked_balance -= amount`); UPDATE ticket; INSERT `audit_log` (`withdraw_cancelled`). |
| `dw.claim_ticket` | Cashier takes a ticket | Validate region match against `cashier_characters`; UPDATE ticket (`status='claimed', claimed_by=…`); INSERT `audit_log`. |
| `dw.release_ticket` | Cashier returns ticket to queue | UPDATE ticket (`status='open', claimed_by=NULL`); INSERT `audit_log`. |
| `dw.add_cashier_character` | `/cashier addchar` | INSERT `cashier_characters`. |
| `dw.remove_cashier_character` | `/cashier removechar` | UPDATE `cashier_characters` (`is_active=false, removed_at=NOW`). |
| `dw.set_cashier_status` | `/cashier set-status` | UPSERT `cashier_status`; manage `cashier_sessions` row. |
| `dw.open_dispute` | Admin opens dispute | INSERT `disputes`; INSERT `audit_log`. |
| `dw.resolve_dispute` | Admin resolves with action | UPDATE `disputes`; possibly call other money-moving fns (e.g., `cancel_withdraw` for refund); INSERT `audit_log`. |
| `dw.ban_user` | Admin bans a user | UPDATE `core.users` (`banned=true`, `banned_reason=…`); INSERT `audit_log`. |
| `dw.unban_user` | Reverse ban | UPDATE `core.users`; INSERT `audit_log`. |
| `dw.treasury_sweep` | Admin records gold removed from in-game guild bank | UPDATE `core.balances[discord_id=0]` (`balance -= amount`); INSERT `audit_log` (`treasury_swept`). |
| `dw.treasury_withdraw_to_user` | Admin moves treasury to a user (for refund or dispute resolution) | UPDATE treasury (`balance -= amount`); UPDATE user balance (`+= amount`); INSERT `audit_log`. |

Every function ends with:
```sql
REVOKE ALL ON FUNCTION dw.<fn> FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dw.<fn> TO goldrush_dw;
```

A SQL injection on the bot cannot escalate to direct `UPDATE core.balances`, only to `EXECUTE` of these functions, all of which encode the integrity rules.

---

## 4. Workflows

### 4.1. Deposit lifecycle

```mermaid
stateDiagram-v2
    [*] --> open: /deposit + modal
    open --> claimed: cashier /claim
    open --> expired: 24h no claim
    open --> cancelled: user /cancel-mine
    claimed --> confirmed: cashier /confirm + 2FA
    claimed --> open: /release (manual or 30m idle auto)
    claimed --> cancelled: cashier /cancel <reason>
    claimed --> expired: 2h since claim w/o action
    confirmed --> [*]: thread archived
    cancelled --> [*]: thread archived
    expired --> [*]: thread archived
    confirmed --> disputed: /admin dispute open
    disputed --> resolved: /admin dispute resolve
    resolved --> [*]
```

**Key invariants:**
- No balance change at `open` — gold has not entered the system yet.
- Balance is credited atomically at `confirm` together with `core.users` row creation (idempotent) and audit log entry.
- `cancel` paths do not touch balance (no gold entered).
- `expired` is identical to `cancel` — no gold entered.

### 4.2. Withdraw lifecycle

```mermaid
stateDiagram-v2
    [*] --> open: /withdraw + modal + balance lock
    open --> claimed: cashier /claim
    open --> expired: 24h no claim → REFUND
    open --> cancelled: user /cancel-mine → REFUND
    claimed --> confirmed: cashier /confirm + 2FA → fee to treasury
    claimed --> open: /release (manual or 30m idle auto)
    claimed --> cancelled: cashier /cancel → REFUND
    claimed --> expired: 2h since claim w/o action → REFUND + admin alert
    confirmed --> [*]: thread archived
    cancelled --> [*]: thread archived
    expired --> [*]: thread archived
    confirmed --> disputed: /admin dispute open
    disputed --> resolved: /admin dispute resolve
    resolved --> [*]
```

**Key invariants:**
- Balance is locked at `open`: `balance -= amount`, `locked_balance += amount`. No gold movement yet outside the bot's records.
- At `confirm`, the lock is finalised as a deduction (`locked_balance -= amount`) and `fee` moves to treasury.
- `amount_delivered = amount − fee` is the amount the cashier delivers in game.
- All non-confirm terminal states (`cancelled`, `expired`) refund the user fully: `balance += amount`, `locked_balance -= amount`.
- No path leaves `locked_balance` permanently elevated; orphan locks are impossible.

### 4.3. Cashier lifecycle and online tracking

- Onboarding: admin assigns `@cashier` role in Discord (or via `/admin promote-cashier`). Cashier registers at least one character via `/cashier addchar`.
- Status: cashier toggles `online`, `offline`, `break` via `/cashier set-status`.
- Sessions: each `online` period is a row in `dw.cashier_sessions`; closes when status changes.
- Auto-offline: a 1h-without-activity background worker closes the session as `expired`.
- Discord disconnect: if Presence Intent is not enabled (default v1), cashiers stay marked online until either they update status manually or the inactivity worker fires.

### 4.4. Background workers

| Worker | Frequency | Action |
|---|---|---|
| `ticket_timeout_worker` | 60 s | Auto-cancel/refund tickets past `expires_at` |
| `claim_idle_worker` | 60 s | Auto-release claimed tickets idle > 30 min; auto-cancel idle > 2h |
| `cashier_idle_worker` | 5 min | Auto-offline cashiers idle > 1h |
| `online_cashiers_embed_updater` | 30 s | Refresh `#online-cashiers` embed |
| `stats_aggregator` | 15 min | Recompute `cashier_stats.avg_claim_to_confirm_s` |
| `audit_chain_verifier` | 6 h | Run `audit_verify.py`; alert on chain break |

All workers are idempotent and resume cleanly after a crash.

### 4.5. Dispute workflow

```mermaid
stateDiagram-v2
    [*] --> open: /admin dispute open
    open --> investigating: /admin dispute set-status investigating
    investigating --> resolved: /admin dispute resolve <action>
    investigating --> rejected: /admin dispute reject <reason>
    open --> resolved: /admin dispute resolve <action>
    open --> rejected: /admin dispute reject <reason>
    resolved --> [*]
    rejected --> [*]
```

Resolution actions: `refund`, `force-confirm`, `partial-refund:<amount>`, `no-action`. Each writes audit log rows; `dw.cashier_stats` updates a "disputes-against" counter on the relevant cashier when the resolution implies cashier fault.

### 4.6. Treasury accounting

The in-game guild bank holds the actual gold. The bot's `core.balances[discord_id=0]` is an accounting record tracking how much gold *should* be in the guild bank as accumulated fee revenue.

**Operational invariant** (must hold at every moment):

```
guild_bank_gold_actual ≥ SUM(all user balances) + treasury_balance
```

When admins physically remove gold from the guild bank, they execute:

```
/admin treasury-sweep amount:<X>
```

Which calls `dw.treasury_sweep(X, admin_id)` — debits `core.balances[0]` and writes an audit row. The guild bank is now lower by exactly that amount, and the bot's records reflect it.

`/admin treasury-withdraw-to-user` is a separate command for using treasury to refund a user (e.g., dispute resolution). It moves gold *within* the bot's records (treasury → user) and does **not** require physical movement in the game.

---

## 5. Discord layer

### 5.1. Slash command catalogue

#### User commands (visible to all)

| Command | Channel | Args |
|---|---|---|
| `/deposit` | `#deposit` | none → modal |
| `/withdraw` | `#withdraw` | none → modal |
| `/balance` | any | none |
| `/cancel-mine` | own ticket thread | none |
| `/help` | any | `topic?:str` |

#### Cashier commands (hidden by default; enabled for `@cashier` via Server Settings → Integrations)

| Command | Channel / context | Args |
|---|---|---|
| `/cashier addchar` | `#cashier-onboarding` | `char:str realm:str region:str faction:str` |
| `/cashier removechar` | `#cashier-onboarding` | `char:str realm:str` |
| `/cashier listchars` | any | none |
| `/cashier set-status` | any | `status:online/offline/break` |
| `/cashier mystats` | any | none |
| `/claim` | inside ticket thread | none |
| `/confirm` | inside ticket thread (claimed by me) | none → 2FA modal |
| `/cancel` | inside ticket thread (claimed by me) | `reason:str` |
| `/release` | inside ticket thread (claimed by me) | none |

#### Admin commands (hidden by default; enabled for `@admin`)

```
/admin setup [--dry-run]
/admin setup-status
/admin set-channel <key> <channel>
/admin reset-setup

/admin cashier-stats <cashier:User>
/admin promote-cashier <user:User>
/admin demote-cashier <user:User>
/admin force-cashier-offline <cashier:User> <reason:str>

/admin set-deposit-limits <min:int> <max:int>
/admin set-withdraw-limits <min:int> <max:int>
/admin set-fee-withdraw <bps:int>

/admin set-deposit-guide        → modal
/admin set-withdraw-guide       → modal

/admin ban-user <user:User> <reason:str>
/admin unban-user <user:User>

/admin dispute open <ticket_uid:str> <reason:str>
/admin dispute resolve <dispute_id:int> <action:str> [<amount:int>]
/admin dispute reject <dispute_id:int> <reason:str>
/admin dispute list [<status:str>]

/admin force-cancel-ticket <ticket_uid:str> <reason:str>
/admin force-close-thread <thread:Channel> <reason:str>

/admin treasury-balance
/admin treasury-sweep <amount:int> <reason:str>                 → 2FA modal
/admin treasury-withdraw-to-user <amount:int> <to_user:User> <reason:str>  → 2FA modal

/admin view-audit [<user:User>] <limit:int>
```

All admin commands carry `@app_commands.default_permissions()` (so they are hidden in autocomplete) and `@require_role("admin")` runtime check.

### 5.2. Channel structure

```
🎰 Casino                           ← Luck owns
   #fairness, game channels, #leaderboard

💰 Banking                          ← D/W owns
   #how-to-deposit                  ← editable embed
   #how-to-withdraw                 ← editable embed
   #deposit                         ← /deposit, threads spawn here
   #withdraw                        ← /withdraw, threads spawn here
   #online-cashiers                 ← live embed every 30s

🛎️ Cashier (private to @cashier + @admin)
   #cashier-alerts                  ← pings on new tickets
   #cashier-onboarding              ← /cashier * commands
   #disputes                        ← admin dispute tracking

🌟 Community                        ← server staff manages
🔒 Staff                            ← @admin only
```

### 5.3. `/admin setup` channel auto-creation

Idempotent admin command that creates the `Banking` and `Cashier` categories plus the eight canonical channels with correct `discord.PermissionOverwrite` for each role.

**Permissions matrix:**

| Channel | @everyone | @cashier | @admin | bot |
|---|---|---|---|---|
| `#how-to-deposit` | View, Read History | + | + | + Manage Messages |
| `#how-to-withdraw` | View, Read History | + | + | + Manage Messages |
| `#deposit` | View, Send, Use App Cmds | + View Private Threads | + | + Manage Threads, Manage Channels |
| `#withdraw` | View, Send, Use App Cmds | + View Private Threads | + | + Manage Threads |
| `#online-cashiers` | View, Read History | + | + | + Manage Messages |
| `#cashier-alerts` | deny View | View, Send, Read History | + | + Send Messages |
| `#cashier-onboarding` | deny View | View, Send, Use App Cmds | + | + |
| `#disputes` | deny View | deny View | View, Send, Read History | + |

The command persists every channel's ID into `dw.global_config` so subsequent runs detect existing channels and reuse them.

### 5.4. Private thread management

Threads are created via `discord.TextChannel.create_thread(type=ChannelType.private_thread, invitable=False)` from the `#deposit` or `#withdraw` parent. The user is added explicitly; `@cashier` role members see the thread because they have `View Private Threads` on the parent channel.

`auto_archive_duration=1440` (24h) is a backup; our workers archive the thread immediately on `confirm`, `cancel`, or `expired`. Archived threads remain visible for audit; they are never deleted.

### 5.5. Modals

All input validated with pydantic. Every dangerous action uses a magic-word confirmation modal.

```python
DepositModal:        char_name, realm, region, faction, amount
WithdrawModal:       char_name, realm, region, faction, amount  (validates balance)
ConfirmTicketModal:  type "CONFIRM"
CancelTicketModal:   reason
EditDynamicEmbedModal: title, description, color, fields_json
DisputeOpenModal:    reason
TreasuryWithdrawConfirmModal: type "TREASURY-WITHDRAW", re-type amount, re-type target
TreasurySweepConfirmModal:    type "SWEEP", re-type amount
```

### 5.6. Embed builders

Themed with the GoldRush palette (Win green, Bust red, Gold, Ember, House blue) inherited from Luck spec §6.3. Builders specific to D/W:

```
deposit_ticket_open_embed
deposit_ticket_claimed_embed
deposit_ticket_confirmed_embed
deposit_ticket_cancelled_embed
withdraw_ticket_open_embed             (shows amount + fee + amount_delivered)
withdraw_ticket_claimed_embed
withdraw_ticket_confirmed_embed
withdraw_ticket_cancelled_embed         (shows REFUNDED indicator)
cashier_alert_embed                    (for #cashier-alerts ping)
online_cashiers_live_embed             (for #online-cashiers; updated every 30s)
cashier_stats_embed                    (ephemeral)
dispute_open_embed                     (for #disputes)
dispute_resolved_embed
how_to_deposit_dynamic_embed           (renders dw.dynamic_embeds row)
treasury_balance_embed                 (admin ephemeral)
no_balance_embed                       (shared with Luck — for new users)
```

### 5.7. Bot startup

1. Connect DB pool with `goldrush_dw` role.
2. Load extensions (cogs).
3. Sync command tree to `GUILD_ID` (instant).
4. Ensure system row + treasury row exist.
5. Ensure dynamic embeds posted (idempotent).
6. Start background workers.
7. Log "ready".

---

## 6. Security model

D/W inherits the 12 pillars of Luck §5 (secrets management, transactional locks, append-only audit log, container hardening, dependency hygiene, code rules, backups, monitoring, etc.). This section adds the D/W-specific considerations.

### 6.1. The economic frontier

D/W is the only system component that mints or destroys user balance outside the game flow. Three layers of human friction guard `confirm`:

1. `claim` (cashier registers ownership of the ticket).
2. In-game trade (real act outside the bot).
3. `confirm` + 2FA modal "Type CONFIRM" (third deliberate human act).

A stolen cashier identity alone cannot drain the system; in-game trades must visibly happen, and discrepancies are detectable in audit logs and via dispute reports from users.

### 6.2. Treasury operations are super-restricted

`treasury-sweep` and `treasury-withdraw-to-user` require:
- `@admin` role (Discord-side and runtime decorator).
- 2FA modal with three checks: magic word, re-typed amount, re-typed target (where applicable).
- Audit log entry tagged `action='treasury_*'`.
- Webhook alert to `#alerts` immediately after execution.

Multi-admin signing is deferred to v1.x; v1 accepts single-admin authority but with maximum forensic traceability.

### 6.3. Cashier accountability

Every cashier's full history is immutable and queryable:
- Ticket history per cashier in `dw.deposit_tickets`, `dw.withdraw_tickets` filtered by `claimed_by`.
- Session history in `dw.cashier_sessions`.
- Aggregated stats in `dw.cashier_stats` (volume, cancel rate, average claim→confirm time).
- Dispute involvement counter in `dw.cashier_stats` derived from `dw.disputes`.

Admins inspect via `/admin cashier-stats @cashier`.

### 6.4. Anti-fraud vectors and mitigations

| Vector | Mitigation v1 |
|---|---|
| Cashier confirms without trading | 2FA modal + dispute mechanism for users + cashier accountability stats |
| User opens fake disputes | Admin reviews each; rejected disputes recorded; repeat offenders blacklisted |
| Stolen Discord account → fake withdraw | `/confirm` 2FA + dispute path if user claims unauthorised |
| Cashier collusion with user (fee evasion) | Audit log immutable; admin spot-checks cashier→user pair concentration |
| Treasury draining via fraudulent disputes | Audit-logged refunds; admin cross-check; multi-admin signing deferred |
| Multi-account abuse (same user, multiple Discord IDs) | Deferred to v1.x (per project decision) |

### 6.5. Required Discord application permissions

D/W needs the same permission set as Luck **plus** `Manage Channels` for `/admin setup`. Specifically:

- Send Messages, Send Messages in Threads
- Embed Links, Attach Files
- Add Reactions, Use External Emojis
- Read Message History
- Use Slash Commands
- Manage Threads
- **Manage Channels** ← new for D/W
- Manage Messages (for editing pinned welcome embeds)

Forbidden: Administrator, Manage Server, Kick, Ban, Manage Roles.

### 6.6. Privileged intents

None. D/W operates entirely on slash command interactions. Cashier-disconnect detection in v1 relies on inactivity workers, not the Presence Intent.

---

## 7. Operations

### 7.1. Compose stack additions

The compose stack from Luck §7.3 already defines `postgres` and `goldrush-luck`. Add the `goldrush-deposit-withdraw` service per §2.3.

### 7.2. VPS setup additions

Beyond the Luck §7.2 setup:

1. Generate `.env.dw` in `/opt/goldrush/secrets/` (Discord token, GUILD_ID, log config) with mode 600 owned by `goldrush:goldrush`.
2. Build the D/W image: `docker compose build goldrush-deposit-withdraw`.
3. Run migrations (creates `dw.*` tables and SECURITY DEFINER fns): `docker compose exec goldrush-luck alembic upgrade head` (Alembic is shared with Luck).
4. Start the D/W service: `docker compose up -d goldrush-deposit-withdraw`.
5. Verify the bot is online: `docker compose logs -f goldrush-deposit-withdraw | grep ready`.
6. In Discord, configure command visibility: enable `/admin *` for `@admin` role and `/cashier *` for `@cashier` role via Server Settings → Integrations → GoldRush Deposit/Withdraw.
7. Run `/admin setup` once to auto-create the `Banking` and `Cashier` categories with correct permissions.

### 7.3. Observability additions

D/W adds Prometheus metrics:

```
goldrush_deposit_tickets_total{status}
goldrush_withdraw_tickets_total{status}
goldrush_deposit_volume_g_total{region}
goldrush_withdraw_volume_g_total{region}
goldrush_treasury_balance_g
goldrush_cashiers_online{region}
goldrush_ticket_claim_duration_s{ticket_type}
goldrush_ticket_confirm_duration_s{ticket_type}
goldrush_cashier_dispute_rate{cashier_id}
goldrush_fee_revenue_g_total
```

Alertmanager rules added to the existing config:

| Alert | Condition |
|---|---|
| GoldRushDWStuckTicket | a ticket is open > 2h |
| GoldRushNoCashiersOnline | tickets opening but zero online cashiers for 10+ min |
| GoldRushTreasuryDrop | treasury drops > 1M G in 1h |
| GoldRushHighCancellationRate | cancellation rate > 20 % in 1h |
| GoldRushUnusualCashierActivity | cashier confirm rate spikes |

Notifications via Discord webhook to a private staff `#alerts` channel.

### 7.4. Backup and retention

D/W tables are included in the existing daily `pg_dump` / GPG-encrypted backup defined in Luck §7.6. Retention 30 days daily + 12 months monthly.

For financial-records compliance, consider 7-year retention for `core.audit_log` and `dw.*_tickets`. Document the operator's chosen retention in `docs/compliance.md`.

### 7.5. Runbook additions

`docs/runbook.md` adds D/W-specific incident playbooks:

| Incident | Procedure |
|---|---|
| No cashiers online for hours | Page cashiers manually; consider auto-message in `#deposit` informing low availability |
| Dispute volume spikes | Cluster by cashier; force-offline if pattern emerges; open investigation |
| Treasury balance growing too large | Schedule periodic in-game guild bank withdrawal + `/admin treasury-sweep` |
| Cashier abandons claimed ticket | Auto-release after 30 min handles most cases; admin reviews repeat offenders |
| User reports gold not received post-confirm | Admin opens dispute, reviews audit log, refunds or blacklists |
| DB role drift | Run `audit_db_grants.py`; re-run init.sql idempotent fragment |

### 7.6. Deploy procedure

Same as Luck §7.5: hot reload for text/embed changes, schema migration for DB changes (with backup beforehand), rollback via git checkout + image rebuild.

### 7.7. Disaster recovery

Inherits Luck §7.8. Additional consideration: the in-game guild bank itself is the ultimate source of truth for actual gold; if the bot's accounting drifts from the guild bank balance, manual reconciliation is required (documented in `docs/runbook.md`).

---

## 8. Testing strategy

### 8.1. Pyramid additions

Roughly +150 unit, +50 integration, +10 property, +5 e2e tests on top of Luck's ~520. Total monorepo target ≈ 735 tests.

### 8.2. Critical D/W test categories

- **Treasury invariant** (property-based): `SUM(user balances) + treasury_balance + admin_swept_total == total_ever_deposited`, holds across thousands of randomised ops.
- **Concurrency**: parallel withdraws/deposits per user; parallel claims of same ticket; `confirm` racing `force-cancel-ticket`.
- **Lifecycle state machine**: every (state, action) pair tested for valid/invalid transitions; terminal states immutable.
- **Cashier permission**: only the claimer can `confirm`/`release`/`cancel`; region-mismatch claim refused; non-cashier cannot claim.
- **2FA modals**: incorrect magic words cancel cleanly; correct words proceed.
- **Worker idempotency**: each background worker can be killed mid-execution and resumed without corruption.
- **Modal validation**: rejects suffixed amounts, non-EU/NA regions, non-Alliance/Horde factions, malformed character names.

### 8.3. Coverage gates

| Module | Minimum |
|---|---|
| `goldrush_core/balance/` | 95 % |
| `goldrush_core/audit/` | 95 % |
| `goldrush_deposit_withdraw/tickets/` | 95 % |
| `goldrush_deposit_withdraw/cashiers/` | 90 % |
| `goldrush_deposit_withdraw/commands/admin_cog.py` | 90 % |
| `goldrush_deposit_withdraw/commands/*` (rest) | 85 % |
| Global monorepo | 85 % |

### 8.4. Cross-bot integration tests

A small but valuable suite verifies Luck and D/W interoperate correctly:

- Deposit → play → withdraw full loop.
- Luck cannot create users or update balances (permission denied as expected).
- Audit-log hash chain remains consistent across operations from both bots.

These run on every PR (testcontainers Postgres is fast enough).

### 8.5. CI pipeline

Same `.github/workflows/ci.yml` as Luck, with additional gates per module above.

---

## 9. Documentation deliverables

Inherits Luck §9 structure. Adds:

```
docs/
├── tickets/
│   ├── deposit-flow.md
│   ├── withdraw-flow.md
│   ├── cashier-onboarding.md
│   ├── ticket-lifecycle.md
│   ├── treasury-management.md
│   ├── disputes.md
│   └── compliance.md
│
├── adr/
│   ├── 0011-dw-as-economic-frontier.md
│   ├── 0012-stateless-deposit-modal.md
│   ├── 0013-private-threads-for-tickets.md
│   ├── 0014-cashier-online-status-model.md
│   ├── 0015-treasury-as-system-account.md
│   ├── 0016-2fa-modals-for-money-ops.md
│   └── 0017-admin-setup-channel-creation.md
│
└── superpowers/specs/
    ├── 2026-04-29-goldrush-luck-v1-design.md
    ├── 2026-04-29-goldrush-luck-v1-implementation-plan.md
    ├── 2026-04-29-goldrush-dw-v1-design.md           ← this file
    └── 2026-04-29-goldrush-dw-v1-implementation-plan.md  ← to be written
```

`docs/security.md` extended with the D/W-specific anti-fraud table.
`docs/runbook.md` extended with D/W incidents.
`docs/observability.md` extended with the new metrics and alerts.
`docs/changelog.md` will track `dw-v1.0.0` separately from `luck-v1.0.0`.

---

## 10. Locked decisions log

| # | Decision | Locked in |
|---|---|---|
| 1 | Scope: deposit + withdraw symmetric in v1 | §1.1 |
| 2 | Tickets via private threads in `#deposit` and `#withdraw` parent channels | §5.4 |
| 3 | Stateless modal: char/realm/region/faction/amount each time, no persistence per user | §5.5 |
| 4 | WoW retail only; EU and NA regions only | §1.2 |
| 5 | Region is a hard filter for cashier–ticket compatibility; faction is informational only | §3.2, §3.3 (`dw.claim_ticket`) |
| 6 | Cashier characters registered via `/cashier addchar` and stored in `dw.cashier_characters` | §3.2 |
| 7 | Cashier online status via `/cashier set-status`; sessions tracked in `dw.cashier_sessions` | §4.3 |
| 8 | First-come-first-served claim among online cashiers | §5.1, §3.3 |
| 9 | Min/max deposit 200 G / 200,000 G; min/max withdraw 1,000 G / 200,000 G | §3.2 |
| 10 | Deposit fee 0 %; withdraw fee 2 %; fee deducted from `amount` (user receives `amount − fee` in game) | §3.3 (`dw.confirm_withdraw`) |
| 11 | Treasury accumulates fees in `core.balances[discord_id=0]`; admins reconcile via `/admin treasury-sweep` after physically retrieving gold from the in-game guild bank | §4.6 |
| 12 | Ticket timeouts: 24h open, 30 min claim idle (auto-release), 2h claim total (auto-cancel + admin alert) | §4.4 |
| 13 | Modal of 5 fields for deposit and withdraw; pydantic-validated; exact integer amount only | §5.5 |
| 14 | 2FA magic-word modals on `/confirm`, `/admin treasury-sweep`, `/admin treasury-withdraw-to-user` | §5.5, §6.2 |
| 15 | Disputes opened by admins only in v1; user-initiated disputes deferred | §1.2, §4.5 |
| 16 | Cashier compensation not automated in v1; tracking only via `dw.cashier_stats` | §1.2 |
| 17 | Multi-account abuse detection deferred to v1.x | §1.2 |
| 18 | `/admin setup` auto-creates the canonical channel structure, idempotent | §5.3 |
| 19 | `#how-to-deposit` and `#how-to-withdraw` content editable via admin commands; rendered from `dw.dynamic_embeds` | §3.2, §5.6 |
| 20 | Bot Discord application requires `Manage Channels` permission for `/admin setup` (in addition to all permissions Luck needs) | §6.5 |
| 21 | No privileged intents in v1; cashier-disconnect detection via inactivity worker only | §6.6 |
| 22 | `/balance` lives in D/W only (not in Luck); Aleix decision 2026-04-29 | §5.1 |
| 23 | Aleix is the sole author of all artefacts; no AI/Anthropic/Claude attribution anywhere | enforced via authorship rule throughout |

---

## 11. Glossary additions

| Term | Meaning |
|---|---|
| Cashier | Discord member with the `@cashier` role, responsible for executing in-game trades |
| Ticket | A deposit or withdraw operation tracked by a private Discord thread + a row in `dw.deposit_tickets` or `dw.withdraw_tickets` |
| Treasury | The system account at `core.balances[discord_id=0]` that holds accumulated fee revenue (accounting record; the actual gold is in the in-game guild bank) |
| Treasury sweep | The act of an admin physically removing gold from the guild bank and reconciling the bot's records via `/admin treasury-sweep` |
| Locked balance | Gold reserved against an in-flight withdraw; subtracted from `balance` and added to `locked_balance`; finalised as a deduction at `confirm` or refunded at `cancel`/`expire` |
| Region | EU or NA — the WoW client region. A hard filter for cashier–ticket compatibility |
| Faction | Alliance or Horde — informational only in retail (cross-faction trading is allowed) |
| Magic word | The case-sensitive string the user must type in a 2FA modal to confirm a dangerous operation (e.g., "CONFIRM", "TREASURY-WITHDRAW") |

---

## 12. Decision log (cross-references)

| Decision | Locked in §  | Rationale |
|---|---|---|
| D/W is the economic frontier; only role with INSERT/UPDATE on core.users/balances | §2.2, §3.1 | Defence in depth; isolates Luck from money-creation paths |
| Stateless WoW identity capture | §1.2, §5.5 | Reduces PII storage; simplifies model |
| Private Discord threads for tickets | §5.4 | Native Discord, clean lifecycle, auto-archive |
| Region as hard filter, faction as informational | §3.3 | Retail allows cross-faction trading; cross-region is technically impossible |
| FCFS claim among online cashiers | §5.1, §3.3 | Simple, fair, low overhead |
| 5-field deposit/withdraw modal | §5.5 | Within Discord limits; pydantic-validated; exact integer amount |
| Withdraw fee deducted from amount (user receives `amount − fee`) | §3.3 | UX: amount means "what comes out of my balance"; fee is implicit |
| Treasury as `discord_id=0` row | §4.6 | Reuses existing `core.balances` schema; no new tables |
| Per-bot DB roles + SECURITY DEFINER frontier | §3.1, §3.3 | Inherited from Luck; SQL-injection cannot mint gold |
| 2FA magic-word modals on money ops | §5.5, §6.2 | Triple human friction on every gold movement |
| `/admin setup` for channel auto-creation | §5.3 | Bootstrap UX; idempotent; reduces operational error |
| Editable welcome embeds in `dw.dynamic_embeds` | §3.2, §5.6 | Admin tunes copy without redeploy |
| No privileged Discord intents | §6.6 | Smaller attack surface; no Discord verification required |
| Manual deploy from VPS in v1 | §7.6 | Inherited from Luck; reduces CI/CD attack surface |
| No multi-account detection in v1 | §1.2 | Explicit project decision; documented gap |
| No partial completions | §1.2 | Reduces complexity; rare cases handled via dispute |
| Aleix as sole author | throughout | Project authorship rule; enforced in commits, docs, embeds, PDFs |

---

## 13. Sign-off

This document is the source of truth for GoldRush Deposit/Withdraw v1 architecture and behaviour. The companion implementation plan (`2026-04-29-goldrush-dw-v1-implementation-plan.md`) decomposes this spec into ordered epics and stories. Any deviation from this spec during implementation requires either (a) a new ADR superseding the relevant decision or (b) a revision of this document with version bump.

— Aleix, 2026-04-29
