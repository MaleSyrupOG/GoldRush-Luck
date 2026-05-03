# Operator runbook — GoldRush → DeathRoll rename

**Date drafted**: 2026-05-03
**Date executed**: 2026-05-03 ~16:30–16:46 UTC (≈16 min downtime)
**Status**: ✅ COMPLETED. See §11 (executor's notes) below for what
actually happened on the live VPS.
**Repo state**: commits 1–6 of the rename refactor have landed on
`main`. The repo IS DeathRoll. The VPS still runs the old
`goldrush-*` containers + `goldrush_*` roles + `/opt/goldrush/`
paths. Until this runbook executes, the next deploy from `main`
WILL FAIL because:

  - Compose tries to start `deathroll-deposit-withdraw` while the
    old `goldrush-dw` container is still up.
  - The bot tries to connect as `deathroll_dw@deathroll` but only
    `goldrush_dw@goldrush` exists in Postgres.
  - The `/opt/deathroll/secrets/.env.dw` referenced in compose
    doesn't exist; the secrets live in `/opt/goldrush/secrets/`.

This runbook coordinates the rename on the live host. Plan for
~15 min of downtime during the cutover.

---

## 1. Pre-flight (no downtime)

On your laptop:

```bash
git pull origin main
git log --oneline | head -10
# Verify the latest 6 commits are the rename refactor and your
# current branch is up to date.
```

On the VPS (`root@91.98.234.106`):

```bash
# Backup the live DB to a safe place (out of the renamed
# directory tree).
docker exec goldrush-postgres pg_dump -U goldrush_admin -Fc goldrush \
    > /root/goldrush-pre-rename-backup.dump

# Optional sanity check: list current state
docker ps --filter name=goldrush
ls /opt/goldrush/
sudo -u postgres true 2>/dev/null \
    || docker exec goldrush-postgres psql -U goldrush_admin -d goldrush \
       -c "SELECT rolname FROM pg_roles WHERE rolname LIKE 'goldrush_%' ORDER BY 1;"
```

Note the role list — should be `goldrush_admin`, `goldrush_dw`,
`goldrush_luck`, `goldrush_poker`, `goldrush_readonly`. If any are
missing, treat the corresponding ALTER ROLE step below as
optional / skipped.

---

## 2. Stop the bot

```bash
docker stop goldrush-dw
# Postgres stays up — we rename roles + DB live.
```

---

## 3. Rename Postgres roles + database

The catch: `ALTER ROLE goldrush_admin RENAME TO ...` cannot run
INSIDE a session connected as `goldrush_admin`. Use the postgres
superuser session inside the container:

```bash
docker exec -i goldrush-postgres psql -U postgres <<'SQL'
ALTER ROLE goldrush_admin    RENAME TO deathroll_admin;
ALTER ROLE goldrush_dw       RENAME TO deathroll_dw;
ALTER ROLE goldrush_luck     RENAME TO deathroll_luck;
-- Skip the next line if goldrush_poker doesn't exist:
ALTER ROLE goldrush_poker    RENAME TO deathroll_poker;
ALTER ROLE goldrush_readonly RENAME TO deathroll_readonly;
ALTER DATABASE goldrush      RENAME TO deathroll;
SQL
```

If `postgres` superuser isn't enabled on your container (some
images disable it), the alternative is to create a temporary
superuser, run the renames as that user, then drop it:

```bash
docker exec -i goldrush-postgres psql -U goldrush_admin -d postgres <<'SQL'
CREATE ROLE _rename_tmp WITH LOGIN SUPERUSER PASSWORD 'TEMP_ONCE';
SQL
# (Then connect as _rename_tmp; run the ALTER ROLE block above;
# then DROP ROLE _rename_tmp.)
```

**Note**: Postgres preserves all GRANT/object ownership through
`ALTER ROLE ... RENAME` because the catalogs link by role OID, not
name. So every per-fn `GRANT EXECUTE ... TO goldrush_dw` survives
the rename and now reads as `deathroll_dw`.

Verify:

```bash
docker exec goldrush-postgres psql -U deathroll_admin -d deathroll \
    -c "SELECT rolname FROM pg_roles WHERE rolname LIKE 'deathroll_%' ORDER BY 1;"
```

Expected: 4 or 5 rows (one per renamed role).

---

## 4. Rename the secrets file paths

```bash
sudo mv /opt/goldrush /opt/deathroll
ls /opt/deathroll/secrets/   # .env.shared and .env.dw should exist
```

Edit the env files in place:

```bash
sudo -u deathroll bash <<'BASH'
sed -i 's|/opt/goldrush/|/opt/deathroll/|g' /opt/deathroll/secrets/.env.shared /opt/deathroll/secrets/.env.dw
BASH
# If .env.dw has POSTGRES_DSN explicitly set, update the DB name:
sudo -u deathroll sed -i 's|@postgres:5432/goldrush|@postgres:5432/deathroll|g' /opt/deathroll/secrets/.env.dw
```

If you have a `goldrush` system user (separate from your login),
rename it too — `sudo usermod -l deathroll goldrush` and update
the home dir + ownership of `/opt/deathroll/`. This step is
optional and depends on your setup.

---

## 5. Rename docker volume (preserve data)

The Postgres data lives on a docker volume. The volume's NAME is
`goldrush_pgdata`. Compose v3 doesn't support renaming volumes
directly, so we use a trick:

```bash
# Stop everything
docker stop goldrush-postgres

# Rename via a copy: spin up a temp container that mounts the old
# volume + a new (auto-created) volume, copy the data, drop the old.
docker volume create deathroll_pgdata
docker run --rm \
    -v goldrush_pgdata:/from \
    -v deathroll_pgdata:/to \
    alpine sh -c 'cd /from && cp -av . /to'

# Verify the copy:
docker run --rm -v deathroll_pgdata:/data alpine ls /data | head -3
# Expected: PG_VERSION, base, global ...

# Now drop the old volume:
docker volume rm goldrush_pgdata
```

Alternative (faster but riskier): `docker volume rename` exists in
some Docker daemons but is undocumented. Skip unless you're
confident.

---

## 6. Rename / recreate containers

The compose service names + container_names are now `deathroll-*`.
Existing containers `goldrush-postgres` + `goldrush-dw` need to be
removed (after all data is on the renamed volume):

```bash
docker rm -f goldrush-postgres goldrush-dw  # noop if already stopped
docker network rm goldrush_net 2>/dev/null   # may not exist if compose removes it
```

Then bring up the new stack:

```bash
cd /opt/deathroll/repo
git pull origin main  # ensure latest

sudo -u deathroll bash -c '
    set -a
    . /opt/deathroll/secrets/.env.shared
    . /opt/deathroll/secrets/.env.dw
    set +a
    docker compose -f ops/docker/compose.yml build deathroll-deposit-withdraw
    docker compose -f ops/docker/compose.yml up -d
'
```

Postgres comes up first (mounting `deathroll_pgdata` which has all
your existing data); the `00-init-roles.sh` + `01-schemas-grants.sql`
init scripts are SKIPPED on existing data dirs (Postgres only
runs them on first init). All tables, rows, audit_log, hash chain
state are preserved.

The bot starts and tries to connect as `deathroll_dw@deathroll` —
which now exists (post-rename) and has all the GRANTs (preserved
by OID through the rename).

---

## 7. Smoke check

```bash
docker ps --filter name=deathroll
docker logs deathroll-dw --tail 30 | grep -E "ready|worker_started|metrics_http"
```

Expected logs:

```
{"command_count": 38, "event": "ready", ...}
{"event": "ticket_timeout_worker_started", ...}
{"event": "claim_idle_worker_started", ...}
{"event": "cashier_idle_worker_started", ...}
{"event": "stats_aggregator_worker_started", ...}
{"event": "audit_chain_verifier_worker_started", ...}
{"port": 9101, "event": "metrics_http_server_started", ...}
{"checked_count": N, "last_verified_id": N, "event": "audit_chain_verified", ...}
```

The `audit_chain_verified` event is the strongest sanity check:
it walks the existing audit_log under the renamed roles and
confirms the HMAC chain is intact end-to-end.

In Discord:

- Run `/balance` — should return the user's pre-rename balance.
- Run `/admin-view-audit user:@you` — should show pre-rename
  audit rows.

If both pass, the rename is successful.

---

## 8. Update the GitHub repo (optional)

✅ **DONE 2026-05-03**: Aleix renamed the repo on the GitHub UI
to `MaleSyrupOG/DeathRoll`. GitHub auto-redirects the old
`MaleSyrupOG/GoldRush-Luck` URL. Both the local clone and the
VPS clone had `git remote set-url` applied:

```bash
git remote set-url origin https://github.com/MaleSyrupOG/DeathRoll.git
```

All in-tree references to the old repo URL (operations.md,
vps_first_setup.sh, changelog, planning specs, the
``DeathRoll-Luck`` mention in this ADR) were updated in a
follow-up commit.

---

## 9. Rollback procedure (if something goes wrong)

If step 7's smoke check fails:

```bash
# Stop the new stack
docker compose -f /opt/deathroll/repo/ops/docker/compose.yml down

# Restore the dump into a fresh DB
docker exec goldrush-postgres pg_restore -U goldrush_admin -d goldrush \
    < /root/goldrush-pre-rename-backup.dump

# Or simpler: revert all the renames
docker exec -i goldrush-postgres psql -U postgres <<'SQL'
ALTER DATABASE deathroll RENAME TO goldrush;
ALTER ROLE deathroll_admin RENAME TO goldrush_admin;
-- ... and so on
SQL

# Move secrets back
sudo mv /opt/deathroll /opt/goldrush

# Check out the pre-rename commit on the repo
cd /opt/goldrush/repo
git checkout 2396a93   # the docs-15.1-15.4 commit before the rename
```

Then file an issue describing what failed, fix, retry.

---

## 10. Post-rename cleanup

Once the new stack has been healthy for 24 h:

```bash
# Drop the safety dump
sudo rm /root/goldrush-pre-rename-backup.dump

# Update operator-side aliases / cron / monitoring that referenced
# the old container or path names. Searching the host:
sudo grep -r "goldrush" /etc/cron.* /usr/local/bin/ /root/ 2>/dev/null
```

If cron jobs scrape the bot's metrics or run `pg_dump` against
`goldrush`, those scripts need editing too.

---

## Sign-off

> **Operator**: Aleix
> **Cutover started**: 2026-05-03 ~16:30 UTC
> **Cutover completed**: 2026-05-03 16:46 UTC
> **Smoke check passed**: 2026-05-03 16:46 UTC
> **24-h watch**: ends 2026-05-04 ~17 UTC

---

## 11. Executor's notes (what actually happened on 2026-05-03)

The runbook executed cleanly with three small surprises worth
recording for next time:

### 11.1. `postgres` superuser path was not enabled

`docker exec -i goldrush-postgres psql -U postgres` failed
because the official `postgres:16-alpine` image only creates the
admin role given by `POSTGRES_USER` (which is `goldrush_admin`).
Took the §3 fallback: created a temporary
`_rename_tmp WITH SUPERUSER` from inside a `goldrush_admin`
session, ran the role / database renames as `_rename_tmp`,
dropped `_rename_tmp`. All renames including `goldrush_admin →
deathroll_admin` completed without disturbing OID-linked GRANTs.

### 11.2. The system user already existed; only the home dir
was missing

`usermod -l goldrush deathroll` had been run during the
`/opt` move, which renamed the user (uid=108) but left the home
dir entry pointing at `/home/deathroll` — a path that didn't
exist. Docker buildx then failed to create `~/.docker` while
building the image. Fix: `mkdir -p /home/deathroll && chown
deathroll:deathroll /home/deathroll`. Build then succeeded.

### 11.3. Old volume could not be dropped while old container
was still defined

`docker volume rm goldrush_pgdata` returned
`volume is in use - [<container-id>]`, even though
`goldrush-postgres` was stopped. Docker holds the mount ref
until the container itself is removed (`docker rm -f`). The
runbook ordering (stop → copy volume → drop old container → drop
old volume) is the right ordering; the issue was that I tried to
drop the volume between the copy and the `rm -f`. Solved by
running `rm -f` first.

### 11.4. Smoke check signals (all green)

Container state:

```
deathroll-dw         Up 8 seconds (healthy)    9101/tcp
deathroll-postgres   Up 13 seconds (healthy)   5432/tcp
```

Bot startup log highlights:

- `db_pool_ready` against `postgres:5432/deathroll` (renamed DB)
- All 6 cogs loaded (account, admin, cashier, deposit, ticket,
  withdraw)
- `command_count: 38` registered with Discord — full v1.0.0
  command surface
- All 3 welcome embeds (`how_to_deposit`, `how_to_withdraw`,
  `cashier_onboarding`) reconciled in-place — proves the bot can
  read+write its own DB rows under the renamed role
- All 7 background workers started: `online_cashiers_updater`,
  `ticket_timeout`, `claim_idle`, `cashier_idle`,
  `stats_aggregator`, `audit_chain_verifier`, `metrics_refresher`
- `metrics_http_server_started` on port 9101
- **`audit_chain_verified` with `last_verified_id: 17`** — the
  HMAC chain successfully validated end-to-end across all
  pre-rename audit rows. This is the strongest possible proof
  that the chain key (`AUDIT_HASH_CHAIN_KEY`) was preserved, the
  audit_log table was preserved (volume copy), and the
  SECURITY DEFINER GRANTs survived (Postgres OID indirection).
- 27 total log lines, 0 at warn/error/critical level

DB state from inside the renamed container:

```
   rolname           tables in core    tables in dw    audit rows
deathroll_admin              4               9            17
deathroll_dw       (alembic_version: 0018_core_list_audit_events)
deathroll_luck
deathroll_readonly
```

Metrics endpoint: 10 metric families with the new `deathroll_`
prefix, all per spec §7.3.

### 11.5. What did NOT need doing

- No re-run of any alembic migration — Postgres skipped
  `00-init-roles.sh` + `01-schemas-grants.sql` because the data
  dir was already initialised, and all tables/rows/triggers came
  along on the renamed volume.
- No re-grant of EXECUTE privileges on SECURITY DEFINER
  functions. Confirmed empirically: the bot's first action
  after boot is `audit_chain_verifier`, which calls
  `core.verify_audit_chain()` as `deathroll_dw`. That call
  succeeded, which is only possible if the rename preserved the
  EXECUTE grant — which it did, by OID.
- No data backfill. Treasury, balances, audit_log, and ticket
  state all carried over verbatim.

### 11.6. Open follow-ups

- The `/root/goldrush-pre-rename-2026-05-03.dump` safety backup
  (121K) stays on disk until 2026-05-04 ~17 UTC, then `rm`.
- ~~The GitHub repo URL is still `MaleSyrupOG/GoldRush-Luck`.~~
  ✅ DONE 2026-05-03: repo renamed on GitHub UI to
  `MaleSyrupOG/DeathRoll`; both local + VPS clones now point at
  the new URL; all in-tree references updated.
- The cron / monitoring sweep (§10) still to do; nothing
  scheduled on the host today, so deferred.
