# DeathRoll — Operator runbook

Cross-cutting incident playbooks. Bot-specific playbooks (D/W today; Luck once it ships) live inline below.

> **General principle**: every step is reversible without irreversible data loss. When in doubt, take a `pg_dump` first. The audit log + the off-site GPG-encrypted backups are your safety net.

---

## 1. Health check protocol

### 1.1. Container state

```bash
ssh root@91.98.234.106 'docker ps --filter name=deathroll'
```

Expected: `deathroll-postgres` and `deathroll-dw` both `Up (healthy)`. The healthchecks are defined in `ops/docker/compose.yml`:

- Postgres: `pg_isready -U $PG_ADMIN_USER -d deathroll`, every 10s.
- D/W: `python -m deathroll_deposit_withdraw.healthcheck`, every 30s. The healthcheck opens a 1-conn pool with 3-second timeout, runs `SELECT 1`, exits 0 only when the result is exactly 1.

### 1.2. Logs

```bash
docker logs deathroll-dw --tail 50
docker logs deathroll-postgres --tail 30
```

A clean boot of `deathroll-dw` has these signals:

- `db_pool_ready` against `postgres:5432/deathroll`
- All 6 cogs loaded (`account`, `admin`, `cashier`, `deposit`, `ticket`, `withdraw`)
- `command_count: 38`
- All 7 background workers started (`ticket_timeout`, `claim_idle`, `cashier_idle`, `online_cashiers_updater`, `stats_aggregator`, `audit_chain_verifier`, `metrics_refresher`)
- `metrics_http_server_started` on port 9101
- `audit_chain_verified` with `last_verified_id` matching `MAX(id) FROM core.audit_log`

### 1.3. The audit chain re-verifier

The strongest single signal. Run on demand:

```
/admin-verify-audit
```

This calls `core.verify_audit_chain()` and reports the last verified id + total checked. A failure surfaces as `audit_chain_broken` log event and an Alertmanager alert.

---

## 2. D/W incident playbooks

The six playbooks specified in D/W spec §7.5.

### 2.1. No cashiers online for hours

**Symptom**: tickets sitting open with no claim; `#online-cashiers` empty; users pinging admins.

**Action**:

1. Check `/admin-cashier-stats` for the cashiers who were last online — when did they last act? Are they on a known break, on holiday?
2. DM 2-3 trusted cashiers to wake up and run `/cashier-online`.
3. If sustained (> 24 h with active demand), consider posting a guild-wide reminder in the cashier channel that pay-shifts are needed.
4. Future enhancement (deferred from v1.0.0): an `auto-message` in `#deposit` informing low availability when cashier count drops to 0.

### 2.2. Dispute volume spikes

**Symptom**: `DeathRollHighDisputeVolume` Alertmanager rule fires (> 5 in 1 h) OR admins notice a cluster.

**Action**:

1. Cluster the recent disputes by cashier_id:

   ```sql
   SELECT
     payload->>'cashier_id' AS cashier_id,
     COUNT(*) AS dispute_count
   FROM core.audit_log
   WHERE action = 'dispute_opened'
     AND created_at > NOW() - INTERVAL '24 hours'
   GROUP BY 1
   ORDER BY 2 DESC;
   ```

2. If a single cashier dominates, run `/admin-force-cashier-offline cashier:@<u>` to take them offline immediately while you investigate.
3. If a single user dominates the opener side, suspect fake disputes; investigate per `disputes.md`.
4. Open an incident report (free-text in Aleix's notes) and resolve each dispute individually per `disputes.md`.

### 2.3. Treasury balance growing too large

**Symptom**: `DeathRollTreasuryHighBalance` rule fires OR `/admin-treasury-balance` shows an amount the operator considers concentrated.

**Action**:

1. Schedule a sweep window — pick a quiet time (low cashier activity).
2. Run `/admin-treasury-sweep amount:<integer>`. The 2FA modal (CONFIRM + amount) ensures no slip-of-the-finger.
3. Out-of-band: move the equivalent gold in-game from the cashier wallet to the operator's guild bank (the bot does not action this; it's the operator's job).
4. The next `audit_chain_verifier` cycle will sign off the sweep audit row.

### 2.4. Cashier abandons claimed ticket

**Symptom**: ticket sitting in `claimed` for > 30 min with no activity; user complaining.

**Action (most cases auto-handle)**:

1. The `claim_idle` background worker (60 s cadence) auto-releases tickets where the channel has had no message for `CLAIM_IDLE_TIMEOUT_SECONDS` (default 1 800 s = 30 min). The ticket goes back to `open` and `cashier-alerts` re-pings.
2. If the auto-release worker has stopped (visible in logs as a long gap with no `claim_idle_release` events), check the worker's last error (`docker logs deathroll-dw --tail 200 | grep claim_idle`). Restart the bot if unhealthy.

**Action (manual)**:

1. `/admin-force-release ticket:GRD-XXXX` — releases the cashier's claim immediately.
2. Optionally `/admin-force-cashier-offline cashier:@<u>` if the cashier abandoned multiple tickets in a row.

### 2.5. User reports gold not received post-confirm

**Symptom**: user opens a dispute saying the cashier confirmed but the trade didn't happen (or was short).

**Action**: full dispute flow per `disputes.md`. Summary:

1. `/admin-dispute-open ticket:GRD-XXXX reason:"..."` opens the dispute.
2. Investigate via the audit log + the cashier's character + the user's character (armory snapshot).
3. Resolve via `/admin-dispute-resolve action:refund_full notes:"..."` (or `refund_partial`, `cashier_warning`, `user_warning`, `ban_user`, `ban_cashier`).
4. If `refund_full` / `refund_partial`, run `/admin-treasury-withdraw-to-user user:@<u> amount:<g> reason:"Dispute GRD-XXXX..."`.
5. If `ban_cashier`: remove the `@cashier` role manually via Server Settings (the bot lacks `Manage Roles`).

### 2.6. DB role drift

**Symptom**: a permission test fails in CI, or a migration ran with the wrong role, or someone manually mutated grants on the live DB.

**Action**:

1. Compare against the canonical state in `ops/postgres/01-schemas-grants.sql`.
2. Run the integration test `test_grant_matrix_separates_minting_from_redistribution` against the live DB (carefully — it expects testcontainers state).
3. For drift, re-apply the idempotent grant fragment by hand (the file is structured so each GRANT can be re-applied without error).
4. If the drift is unexplained, treat as a possible compromise: rotate `PG_ADMIN_PASSWORD`, take a `pg_dump`, and review `core.audit_log` for the rotation window.

---

## 3. Cross-cutting playbooks

### 3.1. Container won't start

```bash
docker logs deathroll-dw --tail 50
```

Common causes:

- **Missing env file** — `.env.shared` or `.env.dw` not present in `/opt/deathroll/secrets/`. The `${PG_ADMIN_USER:?...}` syntax in compose.yml fails fast with a clear message.
- **DB pool fails to open** — Postgres is unhealthy; check its logs first.
- **Discord token invalid** — revoke + regenerate at `discord.com/developers/applications`, paste new token into `.env.dw`, restart.

### 3.2. Postgres won't come up

```bash
docker logs deathroll-postgres --tail 100
```

Common causes:

- **Port conflict** — another postgres bound to 5432. The compose stack uses `deathroll_net` only (no `ports:` exposure), so this only matters in dev where someone added a local-port override.
- **Volume permission** — `deathroll_pgdata` owned by something other than uid 999. Re-set with `docker run --rm -v deathroll_pgdata:/data alpine chown -R 999:999 /data`.
- **Init script fails** — only relevant on FIRST boot of an empty volume. After init, the scripts are skipped.

### 3.3. Audit chain break detected

**Symptom**: `audit_chain_broken` log event from the verifier worker; `/admin-verify-audit` reports a mismatch.

**Action**: this is a SEVERITY 1 event. A clean chain has never been observed to break in production through normal operation; a break implies either a bug in the chain helper or an unauthorised mutation.

1. Take an immediate `pg_dump` of `core.audit_log`:
   ```bash
   docker exec deathroll-postgres pg_dump -U deathroll_admin -t core.audit_log -Fc deathroll > /root/audit_log_emergency_$(date -u +%Y%m%dT%H%M%SZ).dump
   ```
2. Stop the bot to prevent further writes: `docker stop deathroll-dw`.
3. Identify the first broken row: walk the chain manually or via the verifier with verbose logging.
4. Compare against the most recent verified backup; restore the audit_log table if the discrepancy is a single-row mutation.
5. File an incident report. If the mutation was malicious, rotate the chain key and treat the chain as compromised from that point forward.

### 3.4. Disk full on VPS

**Symptom**: `docker logs` shows write errors; postgres healthcheck failing.

**Action**:

1. Check usage: `df -h /`.
2. Common offenders (in order of likely):
   - Old container logs: `docker system prune` reclaims log/file cruft.
   - Postgres `pg_wal/`: a long-running idle transaction can pin WAL. Check `pg_stat_activity`.
   - Backup dumps left in `/root/`: the runbook §10 cleanup script handles this in production.
3. After freeing space, restart the bot if it became unhealthy.

### 3.5. Restoring from backup

The most recent encrypted backup is on the operator's off-site storage. Restore drill procedure:

1. Provision a fresh staging environment (separate VPS or a local docker compose with renamed volumes).
2. Decrypt the dump: `gpg --decrypt nightly-2026-05-03.dump.gpg > nightly.dump`.
3. Restore: `pg_restore -U postgres -d deathroll nightly.dump`.
4. Run the bot against the staging DB with a separate Discord token; verify `audit_chain_verified` succeeds end-to-end.
5. Document the drill in `tests/reports/backup-restore-drill-YYYY-MM-DD.md`.

---

## 4. Deploy procedure

### 4.1. Standard deploy (no DB change)

```bash
ssh root@91.98.234.106
cd /opt/deathroll/repo
sudo -u deathroll git pull origin main
sudo -u deathroll bash -c '
  set -a
  . /opt/deathroll/secrets/.env.shared
  . /opt/deathroll/secrets/.env.dw
  set +a
  docker compose -f ops/docker/compose.yml build deathroll-deposit-withdraw
  docker compose -f ops/docker/compose.yml up -d deathroll-deposit-withdraw
'
docker logs deathroll-dw --tail 30
```

The `restart: unless-stopped` and healthchecks ensure a bad deploy gets caught — if the new container fails its healthcheck, docker keeps restarting it; the operator notices the loop in `docker ps`.

### 4.2. Deploy with schema migration

```bash
# Take a backup first
docker exec deathroll-postgres pg_dump -U deathroll_admin -Fc deathroll > /root/pre-migration-$(date -u +%Y%m%dT%H%M%SZ).dump

# Pull new code
cd /opt/deathroll/repo && sudo -u deathroll git pull origin main

# Run migrations BEFORE rebuilding the bot
docker compose -f ops/docker/compose.yml run --rm deathroll-deposit-withdraw alembic -c ops/alembic/alembic.ini upgrade head

# Then rebuild + restart
sudo -u deathroll docker compose -f ops/docker/compose.yml build deathroll-deposit-withdraw
sudo -u deathroll docker compose -f ops/docker/compose.yml up -d deathroll-deposit-withdraw
```

The migration step is a separate one-shot container so the bot doesn't restart twice.

### 4.3. Rollback

```bash
# Identify the previous-known-good commit
cd /opt/deathroll/repo && git log --oneline | head -10

# Check it out
sudo -u deathroll git checkout <commit>

# Rebuild
docker compose -f ops/docker/compose.yml build deathroll-deposit-withdraw
docker compose -f ops/docker/compose.yml up -d deathroll-deposit-withdraw
```

If the bad version included a schema migration, you'll need to reverse the migration or restore from the pre-migration dump. The runbook for that is the same as §3.5.

---

## 5. Disaster recovery

### 5.1. The VPS is destroyed

1. Spin up a new Hetzner VPS.
2. SSH in as root, run the bootstrap script: `curl -LO https://raw.githubusercontent.com/MaleSyrupOG/DeathRoll/main/ops/scripts/vps_first_setup.sh && chmod +x vps_first_setup.sh && ./vps_first_setup.sh`.
3. Restore the most recent backup per §3.5.
4. Re-add Discord tokens to `/opt/deathroll/secrets/.env.dw`.
5. `docker compose up -d`. Verify the bot rejoins the guild and the audit chain re-verifies.

### 5.2. The bot's accounting drifts from the in-game guild bank

The in-game guild bank is the ULTIMATE source of truth for actual gold. If the bot's `core.balances` sum + treasury + admin-swept-total disagrees with the guild bank balance:

1. Compute the discrepancy: `(SUM(core.balances) + admin_swept_history) - guild_bank_balance`.
2. Generate a SQL audit-log dump and walk the unconfirmed/disputed tickets. Look for `confirmed` audit rows whose corresponding in-game trade can't be verified.
3. The reconciliation strategy is to make the bot agree with the guild bank — never the other way around. If the bot says `confirmed` but no trade happened, the cashier owes the operator that gold (or the operator absorbs it as a loss). Document the reconciliation in an incident report.

---

## 6. References

- D/W design spec §7.5 (incident playbooks)
- D/W design spec §7.7 (disaster recovery)
- `compliance.md` — retention and audit posture
- `security.md` — security pillars
- `observability.md` — Alertmanager rules + metrics
- `operations.md` — VPS setup + deploy
- ADR 0011, 0015 (the economic frontier and treasury invariant)
