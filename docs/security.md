# DeathRoll — Security posture

This is the cross-cutting security overview for the DeathRoll platform. Bot-specific anti-fraud and operational details are referenced inline.

---

## 1. Threat model

DeathRoll mediates real-value gambling (WoW Gold) over a Discord interface. The threats we defend against, in priority order:

1. **Inflation attacks** — anyone (a compromised bot, a malicious operator role, a SQL-injection vector) attempts to mint gold-G outside the deposit/withdraw flow.
2. **Cashier confirms without trading** — a cashier claims a deposit ticket, types `CONFIRM`, but never traded the gold in-game.
3. **User claims unauthorised withdraw** — a stolen Discord account is used to drain the victim's balance.
4. **Treasury draining** — fraudulent disputes loop money out of the treasury to a colluding user.
5. **Audit-log tampering** — an attacker with database access tries to delete or mutate audit rows.
6. **Slip-of-the-finger admin error** — admin accidentally executes a million-G operation off by a zero.
7. **Token / secret exfiltration** — Discord token, audit chain key, or DB password leaks into logs, monitoring, or backups.

---

## 2. The 12 security pillars

Inherited from Luck spec §5; D/W extends them per spec §6.

1. **Secrets management** — every secret typed `SecretStr` in `deathroll_core/config/__init__.py`; consumed via `.get_secret_value()` at the use site only; never logged. Secrets live in `/opt/deathroll/secrets/.env.shared` and `/opt/deathroll/secrets/.env.dw` (mode 600, owned `deathroll:deathroll`).
2. **Transactional locks** — every gold-moving operation runs inside a SECURITY DEFINER fn that takes `FOR UPDATE` row locks on `core.balances` and ticket rows.
3. **Append-only audit log** — `core.audit_log` enforced immutable at trigger level; HMAC-SHA256 hash chain verifiable end-to-end (see `compliance.md` §2).
4. **Container hardening** — Docker images run as non-root user (`1002:1002`), `read_only: true`, `cap_drop: ALL`, `no-new-privileges`, `pids_limit: 256`, `mem_limit: 384m`. Postgres equivalent in compose.yml.
5. **Dependency hygiene** — `pip-audit --strict` runs in CI and blocks PRs on new advisories. The launch security review (`docs/security-review-dw-2026-05-03.md`) documents the one accepted-risk (pytest 8.3.4 dev-only).
6. **Code rules** — mypy strict + ruff baseline clean across every source file. Pydantic v2 with `extra='forbid'` on every input contract.
7. **Backups** — nightly `pg_dump` + GPG-encryption + off-site copy. Quarterly restore drill recommended.
8. **Monitoring** — Prometheus exposition on `:9101/metrics`; Alertmanager rules for treasury drops, dispute spikes, cashier anomalies. See `observability.md`.
9. **Authentication** — Discord OAuth-derived bot tokens (no operator-controlled passwords inside the bot). Per-bot Postgres roles enforce schema-level access.
10. **Authorisation** — slash commands check role membership at the cog layer (`@app_commands.default_permissions(administrator=True)` for admin commands). SECURITY DEFINER fns check actor ids inline.
11. **2FA modals** — every money-moving operation requires a re-typed magic word (and amount/recipient where applicable). See ADR 0016.
12. **Privileged intents** — NONE. Neither D/W nor (by spec) Luck request the Presence Intent or Message Content Intent. Slash-command-only design.

---

## 3. D/W-specific anti-fraud table

The following maps each anti-fraud vector identified in spec §6.4 to its mitigation in v1.0.0:

| Vector | Mitigation v1.0.0 | Where enforced |
|---|---|---|
| **Cashier confirms without trading** | (a) `/confirm` 2FA modal (`CONFIRM` magic word). (b) Dispute mechanism for users who didn't receive gold. (c) `cashier_dispute_rate` metric flags repeat offenders. | `/confirm` cog handler + `dw.confirm_*` SDFs + `disputes.md` flow |
| **User opens fake disputes** | (a) Admin reviews each dispute manually before resolving. (b) `dispute_rejected` action records bad-faith filings. (c) Repeat offenders get `/admin-ban-user`. | `dw.open_dispute` requires admin actor; `dw.reject_dispute` + `dw.ban_user` |
| **Stolen Discord account → fake withdraw** | (a) `/withdraw` modal forces re-typed character + region + faction (so a stolen account can't drain to an unfamiliar character). (b) `/confirm` 2FA modal. (c) Dispute path for the real owner once they regain access. | Modal field validation + `dw.confirm_withdraw` + `disputes.md` |
| **Cashier collusion with user (fee evasion)** | (a) Audit log immutable; admin spot-checks cashier→user pair concentration. (b) `cashier_dispute_rate` and `confirms_per_hour` per cashier surfaced via `/admin-cashier-stats`. | Statistical inspection; deferred to v1.x for an automatic detector |
| **Treasury draining via fraudulent disputes** | (a) Refund actions (`refund_full`, `refund_partial`) require admin judgement and `/admin-treasury-withdraw-to-user` 3-input modal. (b) `DeathRollTreasuryDrop` Alertmanager rule fires on > 1 M G drop in 1h. (c) Multi-admin signing deferred to v1.x. | Treasury SDFs + 2FA modals + Alertmanager |
| **Multi-account abuse (same user, multiple Discord IDs)** | Deferred to v1.x. v1.0.0 has no cross-account linking detection. | n/a |

---

## 4. The economic frontier discipline

D/W is the only system component that can mint or destroy `core.balances` rows. The Luck and Poker bots can ONLY redistribute existing balance via game-settlement SDFs. Enforced by:

- **Postgres role grants** — `deathroll_dw` is the only role with EXECUTE on `dw.confirm_deposit` and `dw.confirm_withdraw`.
- **Three-layer human friction at confirm** — claim, in-game trade, confirm + 2FA modal.
- **Treasury invariant** — `SUM(core.balances.balance_g) == total_ever_deposited - total_ever_swept`. Pinned in property test.

See ADR 0011 for the full reasoning.

---

## 5. Secret redaction

Every secret is typed `SecretStr` (Pydantic v2). The four secrets:

- `postgres_dsn`
- `button_signing_key`
- `audit_hash_chain_key`
- `discord_token`

Each is accessed via `.get_secret_value()` only at the consumption site:

- `client.py:131` — DSN passed to `asyncpg.create_pool`. Logging goes through `_redact_dsn` (`client.py:345`) which strips `user:pw@`.
- `__main__.py:61` — Discord token passed to `bot.start`. Never logged directly.

Verified empirically: the boot log emits `{"dsn_host": "postgres:5432/deathroll", "event": "db_pool_ready"}` — no password, no chain key. See `docs/security-review-dw-2026-05-03.md` §3.

---

## 6. The audit chain

`core.audit_log` rows form a hash chain:

```
chain_link_n = HMAC-SHA256(audit_hash_chain_key, chain_link_{n-1} || canonical(row_n))
```

`chain_link_0` is a fixed seed at install time. Every subsequent row's `chain_link` is computed inside the same transaction that inserts the row, via `core.audit_log_insert_with_chain()`.

**Verification**:

- The `audit_chain_verifier` background worker re-validates the chain every 6 h by re-walking from the last verified id forward.
- `/admin-verify-audit` triggers an on-demand re-walk.
- The published verifiers (`verifier/python/` and the planned `verifier/node/`) let an external auditor independently verify the chain given the chain key.

The chain key (`AUDIT_HASH_CHAIN_KEY`) is in `.env.shared` and rotates with the rest of the secrets per the operator's rotation cadence. **Rotating the key invalidates all existing chain verification** — the operator must re-anchor the chain by re-computing every link with the new key (a one-time migration). For v1.0.0 the chain key is treated as long-lived; rotation procedure is out-of-scope.

---

## 7. Privileged intents (none)

The bot operates on slash commands only. `intents.message_content = False`, `intents.presences = False`. The `intents.guilds = True` and `intents.members = True` (the latter strictly for resolving Discord usernames inside slash command handlers, not for tracking presence) are the only privileged intents enabled, and they are NOT considered "Privileged Gateway Intents" by Discord — they're standard.

This means:

- The bot does NOT need Discord's "Verified" status for the privileged-intent surface.
- The bot's permissions list (Send Messages, Manage Channels, etc.) is the entire attack surface; no parallel "ingest all member status changes" channel.

See ADR 0014 (cashier online status — bot-state, not presence) for the related decision.

---

## 8. Container security

The compose stack hardens both Postgres and the D/W bot:

```yaml
deathroll-dw:
  user: "1002:1002"           # non-root
  read_only: true             # filesystem read-only
  tmpfs: [/tmp:size=64m]      # writable tmp only in tmpfs
  cap_drop: [ALL]             # no Linux capabilities
  security_opt:
    - no-new-privileges:true
  pids_limit: 256
  mem_limit: 384m
```

Postgres is restricted to `deathroll_net` only (no `ports:` exposure). `cap_drop: ALL` + `cap_add: [CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE]` keeps only what postgres needs to chown its data dir.

Both containers have `restart: unless-stopped`, healthchecks, and JSON file logging capped at modest sizes.

---

## 9. Dependency audit posture

`pip-audit --strict` runs in CI on every PR. Current state per the launch security review (`docs/security-review-dw-2026-05-03.md`):

- 1 known accepted-risk: pytest 8.3.4 GHSA-6w46-j5rx-g56g (dev-only; pytest never runs in production). Bump to 9.x deferred post-launch.
- Pillow + types-Pillow dropped during launch prep (Luck banner generator paused; not imported anywhere).

The CI gate fails on any new advisory. Adding a new dependency triggers the gate before merge.

---

## 10. Re-review trigger

Re-run the launch security review (`docs/security-review-dw-YYYY-MM-DD.md`) when ANY of:

- A new SECURITY DEFINER function lands.
- A change to the `core.audit_log` triggers or `core.audit_log_insert_with_chain`.
- A change to the role / grant matrix.
- A new secret added to `DwSettings` or `LuckSettings`.
- pip-audit output changes shape (new advisory in our deps).

Subsequent reviews live alongside the original at `docs/security-review-dw-YYYY-MM-DD.md`.

---

## 11. References

- ADR 0011, 0012, 0013, 0014, 0015, 0016, 0017
- D/W design spec §6 (security model)
- Luck design spec §5 (the 12 pillars)
- `docs/security-review-dw-2026-05-03.md` — launch sign-off
- `compliance.md` — retention and PII posture
- `runbook.md` — incident response
- `observability.md` — alerts and metrics
