# Backup and Restore — DeathRoll

## Overview

Daily encrypted backups of the entire `deathroll` Postgres database. Encryption uses a GPG key generated on the VPS during the first-time setup; the public key fingerprint must be stored OUTSIDE the VPS (1Password / hardware token) so it can be authenticated on a new VPS in case of disaster.

## Schedule

- **Daily** at 03:00 UTC, written to `/opt/deathroll/backups/daily/`. Retention: 30 days.
- **Monthly** on the 1st of each month, copied to `/opt/deathroll/backups/monthly/`. Retention: ~12 months.
- **Optional offsite** rsync to a Storage Box if `/root/.ssh/storagebox_key` and `STORAGEBOX_HOST` are configured.

The cron entry lives at `/etc/cron.d/deathroll-backup` and is installed by:

```bash
sudo cp /opt/deathroll/repo/ops/scripts/deathroll-backup.cron /etc/cron.d/deathroll-backup
sudo chmod 644 /etc/cron.d/deathroll-backup
sudo systemctl reload cron 2>/dev/null || sudo service cron reload
```

## Manual backup

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106 \
    /opt/deathroll/repo/ops/scripts/backup.sh
```

Verify:

```bash
ls -la /opt/deathroll/backups/daily/
# expected: a fresh deathroll-<timestamp>.dump.gpg
```

## Restore — production (DESTRUCTIVE)

> This DROPS the entire `deathroll` database and replaces it with the contents of the chosen archive.

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
ls /opt/deathroll/backups/daily/    # find the archive you want
/opt/deathroll/repo/ops/scripts/restore.sh \
    /opt/deathroll/backups/daily/deathroll-2026-05-01T03-00-00Z.dump.gpg
# (you will be asked to type RESTORE-PRODUCTION to confirm)
```

The script:
1. Asks for confirmation (skip with `RESTORE_FORCE=1` only in extremely well-understood cases).
2. Stops the D/W bot.
3. Drops and recreates the deathroll database.
4. Decrypts the archive into `/tmp/restore-$$.dump` (mode 600).
5. Pipes through `pg_restore`.
6. Shreds the temporary dump.
7. Restarts the bot.

## Restore drill (NON-destructive, into a temp DB)

To verify a backup is restorable without touching production:

```bash
ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
RESTORE_DB=deathroll_drill \
    /opt/deathroll/repo/ops/scripts/restore.sh \
    /opt/deathroll/backups/daily/deathroll-2026-05-01T03-00-00Z.dump.gpg

# verify some rows are present
docker compose -f /opt/deathroll/repo/ops/docker/compose.yml exec postgres \
    psql -U deathroll_admin -d deathroll_drill -c "
    SELECT COUNT(*) FROM core.users;
    SELECT COUNT(*) FROM core.audit_log;"

# clean up
docker compose -f /opt/deathroll/repo/ops/docker/compose.yml exec postgres \
    psql -U deathroll_admin -d postgres -c "DROP DATABASE deathroll_drill;"
```

## Drill cadence

Run the restore drill **every quarter** to confirm backups are restorable. Document the timing and result in `docs/runbook.md`.

## If the GPG key is lost

The encrypted backups are useless without the GPG private key. Defences:

1. The key lives in the VPS root user's keyring; it is NOT in the repo or in any local file by default.
2. The fingerprint is also stored OUTSIDE the VPS (1Password / hardware token) so we can identify the key after a VPS rebuild.
3. The public key was exported to `/opt/deathroll/secrets/backup-gpg-public.asc` after first setup; download a copy to your local machine for safekeeping (it doesn't need to be secret).

If both the VPS AND your local copy of the fingerprint are lost: the backups become unrecoverable. This is by design — encryption with no key is the same as no backup. Don't lose the fingerprint.
