# Operations — GoldRush platform

> Status: living document. Ground truth for VPS setup, deploy, secrets handling, and routine operational tasks. Every command in this file is **literal** and intended to be copy-pasted.

## Conventions

- **VPS host:** `91.98.234.106` (Hetzner). Hostname: `infinity-boost-srvr`.
- **SSH for admin work** (setup, deploy, backup, restore, anything that touches `/opt/goldrush/`):
  ```bash
  ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
  ```
  The default identity (`~/.ssh/id_ed25519`) is authorised for `root` on this VPS.
- **SSH for read-only inspection** (logs, top, free, etc.) without touching system state:
  ```bash
  ssh sdr-agentic
  ```
  This is the existing config alias for unprivileged user `sdr`.
- **Project root on VPS:** `/opt/goldrush/`.
- **Operational user on VPS:** `goldrush` (system user, member of `docker` group). Created by `vps_first_setup.sh`.
- **Repository:** `https://github.com/MaleSyrupOG/GoldRush-Luck.git` (the monorepo for all three bots).

---

## 1. First-time VPS setup (one-shot)

Done **once** on the VPS. The script is idempotent so re-running is safe.

### 1.1. Verify SSH access

From your local machine:

```bash
ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=10 root@91.98.234.106 'whoami; hostname; id'
```

Expected output:

```
root
infinity-boost-srvr
uid=0(root) gid=0(root) groups=0(root)
```

If you see anything else, fix SSH access before continuing.

### 1.2. Run the setup script

You can run the setup script in two ways:

**Option A — pull-and-run (recommended; uses the version on `main`):**

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106 'bash -s' <<'REMOTE'
set -e
apt-get update -qq && apt-get install -y -qq git gpg
cd /tmp
curl -fsSL -o vps_first_setup.sh \
    https://raw.githubusercontent.com/MaleSyrupOG/GoldRush-Luck/main/ops/scripts/vps_first_setup.sh
chmod +x vps_first_setup.sh
./vps_first_setup.sh
REMOTE
```

**Option B — manual SSH session (for visibility):**

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
# (now on the VPS as root)
apt-get update && apt-get install -y git gpg
cd /tmp
curl -LO https://raw.githubusercontent.com/MaleSyrupOG/GoldRush-Luck/main/ops/scripts/vps_first_setup.sh
chmod +x vps_first_setup.sh
./vps_first_setup.sh
```

The script prints a step-by-step transcript (`[1/8]`, `[2/8]`, …). When it finishes, it tells you exactly what to do next.

### 1.3. Edit the placeholder Discord token

The setup script staged `/opt/goldrush/secrets/.env.dw` with `PASTE_*` placeholders. You must replace them with the real values from your local `dwBotKeys.txt`:

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
sudo -u goldrush nano /opt/goldrush/secrets/.env.dw
```

In the editor:

- Replace `PASTE_YOUR_DW_BOT_TOKEN_HERE` with the bot token from your local `dwBotKeys.txt`.
- Replace `PASTE_YOUR_DISCORD_GUILD_ID_HERE` with the GoldRush Discord server (guild) ID.

Save (`Ctrl+O`, Enter) and exit (`Ctrl+X`).

Then verify both placeholders are gone:

```bash
sudo -u goldrush grep -c PASTE_ /opt/goldrush/secrets/.env.dw
# expected output: 0
```

If the count is anything other than `0`, you missed a placeholder; re-edit.

### 1.4. Re-verify perms (some editors break perms on save)

```bash
ls -la /opt/goldrush/secrets/.env.dw
# expected: -rw------- 1 goldrush goldrush ... .env.dw
```

If the perms are wrong, fix them:

```bash
chmod 600 /opt/goldrush/secrets/.env.dw
chown goldrush:goldrush /opt/goldrush/secrets/.env.dw
```

### 1.5. Save the GPG fingerprint OFF the VPS

The setup script printed a line like:

```
Fingerprint: AAAA1111BBBB2222CCCC3333DDDD4444EEEE5555
```

**Copy this fingerprint into your password manager (1Password, Bitwarden, KeePassXC, or a hardware-secured note).** If the VPS ever dies or the GPG key needs to be reinstalled, you will need this fingerprint to identify and authenticate the key on the new VPS.

The public key is also saved to `/opt/goldrush/secrets/backup-gpg-public.asc` if you want a copy:

```bash
scp -i ~/.ssh/id_ed25519 root@91.98.234.106:/opt/goldrush/secrets/backup-gpg-public.asc \
    ~/Documents/goldrush-backup-pubkey.asc
```

---

## 2. First-time deployment of the stack

Once the VPS setup is done and `.env.dw` has real values, deploy the bot.

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
cd /opt/goldrush/repo
sudo -u goldrush git pull origin main

# Build images and start the stack
sudo -u goldrush docker compose \
    --env-file /opt/goldrush/secrets/.env.shared \
    -f ops/docker/compose.yml up -d --build
```

The first build takes 2–4 minutes (downloading the Python 3.12 base, building the `uv` venv, etc.). Subsequent builds are seconds because Docker layer cache.

### 2.1. Verify Postgres is up and healthy

```bash
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml ps
# expected: goldrush-postgres State=running, Health=healthy
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml exec postgres \
    pg_isready -U goldrush_admin -d goldrush
# expected: accepting connections
```

### 2.2. Verify schemas and roles

```bash
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml exec postgres \
    psql -U goldrush_admin -d goldrush -c "
    SELECT schema_name FROM information_schema.schemata
    WHERE schema_name IN ('core','fairness','luck','dw','poker') ORDER BY 1;"
# expected: 5 rows
```

```bash
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml exec postgres \
    psql -U goldrush_admin -d goldrush -c "
    SELECT rolname FROM pg_roles WHERE rolname LIKE 'goldrush_%' ORDER BY 1;"
# expected: goldrush_admin, goldrush_dw, goldrush_luck, goldrush_readonly
# (goldrush_poker is intentionally absent until we enable it)
```

### 2.3. Verify the D/W bot container is running

```bash
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml logs --tail=20 goldrush-deposit-withdraw
# expected: "[goldrush_deposit_withdraw] placeholder process running; ..."
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml ps goldrush-deposit-withdraw
# expected: State=running, Health=healthy
```

The placeholder bot does not yet do anything useful in Discord — it just stays alive. The full implementation (slash commands, ticket flows, etc.) lands in Epics 4-13 of the implementation plan.

---

## 3. Routine deployments (after the first one)

Most deploys land via `git pull` + rebuild:

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
cd /opt/goldrush/repo
sudo -u goldrush git pull origin main

sudo -u goldrush docker compose \
    --env-file /opt/goldrush/secrets/.env.shared \
    -f ops/docker/compose.yml up -d --build goldrush-deposit-withdraw

sudo -u goldrush docker compose -f ops/docker/compose.yml logs --tail=50 -f goldrush-deposit-withdraw
```

Downtime: ~5 seconds (container restart).

### 3.1. Schema migrations

When a PR adds an Alembic migration:

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
cd /opt/goldrush/repo
sudo -u goldrush git pull origin main

# 1. Take a backup BEFORE migrating
/opt/goldrush/repo/ops/scripts/backup.sh

# 2. Preview the migration as raw SQL (safety check)
sudo -u goldrush docker compose -f ops/docker/compose.yml exec goldrush-deposit-withdraw \
    alembic -c ops/alembic/alembic.ini upgrade head --sql > /tmp/migration_preview.sql
less /tmp/migration_preview.sql
# (review carefully before continuing)

# 3. Stop the bot so it doesn't read partial schema during the migration
sudo -u goldrush docker compose -f ops/docker/compose.yml stop goldrush-deposit-withdraw

# 4. Run the migration as a one-shot
sudo -u goldrush docker compose -f ops/docker/compose.yml run --rm goldrush-deposit-withdraw \
    alembic -c ops/alembic/alembic.ini upgrade head

# 5. Build and restart the bot
sudo -u goldrush docker compose \
    --env-file /opt/goldrush/secrets/.env.shared \
    -f ops/docker/compose.yml up -d --build goldrush-deposit-withdraw
```

Downtime: 30 s to a few minutes depending on migration size.

### 3.2. Rollback

If something is wrong after a deploy:

```bash
cd /opt/goldrush/repo
sudo -u goldrush git log --oneline -10            # find the previous good SHA
sudo -u goldrush git checkout <previous-good-sha>
sudo -u goldrush docker compose \
    --env-file /opt/goldrush/secrets/.env.shared \
    -f ops/docker/compose.yml up -d --build goldrush-deposit-withdraw
```

If the rollback also requires reversing a schema migration:

```bash
sudo -u goldrush docker compose -f ops/docker/compose.yml exec goldrush-deposit-withdraw \
    alembic -c ops/alembic/alembic.ini downgrade -1
```

If the rollback is impossible because data has been written that doesn't fit the old schema, restore from the backup taken in §3.1 — see `docs/backup-restore.md`.

---

## 4. Configuring command visibility in Discord (one-shot)

After the bot is online, the server owner must enable the visibility of the privileged slash commands per role. This is a Discord-side config that the bot cannot do for you.

Go to the GoldRush server → `Server Settings` → `Integrations` → `GoldRush Deposit/Withdraw` → `Manage`.

For each command starting with `/admin`, click it and add an override:

- Roles & Members: `@admin = Allow`.

For each command starting with `/cashier`, click it and add an override:

- Roles & Members: `@cashier = Allow`.

User-facing commands (`/deposit`, `/withdraw`, `/balance`, `/help`, `/cancel-mine`) need no overrides — they are visible to everyone by default.

Run `/admin setup` once in the server to auto-create the canonical channel structure (`#deposit`, `#withdraw`, `#online-cashiers`, `#cashier-alerts`, etc.) with the correct per-role permissions. The command is idempotent.

---

## 5. Daily backups

The setup script does NOT install the cron entry automatically. To enable daily backups:

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
sudo cp /opt/goldrush/repo/ops/scripts/goldrush-backup.cron /etc/cron.d/goldrush-backup
sudo chmod 644 /etc/cron.d/goldrush-backup
sudo systemctl reload cron 2>/dev/null || sudo service cron reload
```

Verify the backup runs:

```bash
# trigger an immediate backup as a smoke test
/opt/goldrush/repo/ops/scripts/backup.sh
ls -la /opt/goldrush/backups/daily/
# expected: at least one .dump.gpg file with a recent timestamp
```

Restore drill: see `docs/backup-restore.md`.

---

## 6. Logs and observability

```bash
# tail the D/W bot
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106 \
    'sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml logs -f goldrush-deposit-withdraw'

# Postgres
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106 \
    'sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml logs -f postgres'

# Loki / Grafana / Alertmanager: shared with the existing sdr-agentic stack;
# see docs/observability.md for dashboards (built incrementally).
```

---

## 7. Stopping and starting the stack

```bash
# stop everything (Postgres + D/W)
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml down

# stop only the D/W bot, keep Postgres up
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml stop goldrush-deposit-withdraw

# start everything again
sudo -u goldrush docker compose \
    --env-file /opt/goldrush/secrets/.env.shared \
    -f /opt/goldrush/repo/ops/docker/compose.yml up -d
```

### 7.1. Wipe the local database (TEST/DRILL ONLY)

> Never on production. This destroys all data. Used for restore drills.

```bash
sudo -u goldrush docker compose -f /opt/goldrush/repo/ops/docker/compose.yml down -v
```

The `-v` flag removes the `goldrush_pgdata` volume.

---

## 8. Local development

For dev work on Aleix's Windows machine, use a separate local secrets directory (gitignored). The repo includes a `.local-dev/secrets/` template path used during testing of the `init.sql` and compose:

```bash
# from repo root, create local dev secrets
mkdir -p .local-dev/secrets
cat > .local-dev/secrets/.env.shared <<'EOF'
PG_ADMIN_USER=goldrush_admin
PG_ADMIN_PASSWORD=local_dev_admin
PG_LUCK_PASSWORD=local_dev_luck
PG_DW_PASSWORD=local_dev_dw
PG_READONLY_PASSWORD=local_dev_ro
PG_POKER_PASSWORD=disabled
BUTTON_SIGNING_KEY=local_dev_button
AUDIT_HASH_CHAIN_KEY=local_dev_audit
EOF
cat > .local-dev/secrets/.env.dw <<'EOF'
DISCORD_TOKEN_DW=disabled-for-local-dev
GUILD_ID=0
LOG_LEVEL=debug
LOG_FORMAT=text
EOF

# bring up Postgres (without the bot, since the bot needs a real Discord token)
ENV_DIR="$(pwd)/.local-dev/secrets" docker compose \
    --env-file .local-dev/secrets/.env.shared \
    -f ops/docker/compose.yml up -d postgres
```

Tear down:

```bash
ENV_DIR="$(pwd)/.local-dev/secrets" docker compose \
    --env-file .local-dev/secrets/.env.shared \
    -f ops/docker/compose.yml down -v
```

`.local-dev/` is git-ignored. Real secrets never go there; only invented ones for local testing.
