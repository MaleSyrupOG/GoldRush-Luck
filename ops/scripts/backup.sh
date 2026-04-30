#!/usr/bin/env bash
# =============================================================================
# GoldRush — Postgres daily backup script.
#
# Runs as root via /etc/cron.d/goldrush-backup.
#
# What it does:
#   1. pg_dump of the entire `goldrush` database in custom format (-Fc)
#      executed inside the goldrush-postgres container.
#   2. Pipes the dump into `gpg --encrypt --recipient "GoldRush Backup"` and
#      writes the encrypted archive to /opt/goldrush/backups/daily/.
#   3. On the 1st of each month, copies the daily backup to monthly/.
#   4. Verifies the archive size > 1 KB and that the GPG header is valid.
#   5. Prunes daily backups older than 30 days, monthly backups older than
#      ~12 months.
#   6. (Optional) rsync to an off-site Storage Box if /root/.ssh/storagebox_key
#      exists. This is wired but optional in v1.
#
# Idempotent: safe to run multiple times in the same day; just creates a new
# timestamped archive.
# =============================================================================

set -euo pipefail

BACKUP_DIR="/opt/goldrush/backups"
RETENTION_DAYS=30
RETENTION_MONTHS=12
GPG_RECIPIENT="GoldRush Backup"
COMPOSE_FILE="/opt/goldrush/repo/ops/docker/compose.yml"
ENV_FILE="/opt/goldrush/secrets/.env.shared"

if [ ! -f "${COMPOSE_FILE}" ]; then
    echo "Error: compose file not found at ${COMPOSE_FILE}" >&2
    exit 1
fi
if [ ! -f "${ENV_FILE}" ]; then
    echo "Error: env file not found at ${ENV_FILE}" >&2
    exit 1
fi

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DAILY_FILE="${BACKUP_DIR}/daily/goldrush-${TS}.dump.gpg"
MONTH_TAG="$(date -u +%Y-%m)"
MONTHLY_FILE="${BACKUP_DIR}/monthly/goldrush-${MONTH_TAG}.dump.gpg"

mkdir -p "${BACKUP_DIR}/daily" "${BACKUP_DIR}/monthly"

# 1. dump (inside the postgres container) → 2. encrypt → 3. write
# We read PG_ADMIN_USER from the env file so the dump runs as the admin role.
# shellcheck disable=SC1090
source "${ENV_FILE}"
: "${PG_ADMIN_USER:?PG_ADMIN_USER must be set}"

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
    pg_dump --username "${PG_ADMIN_USER}" --dbname goldrush -Fc --no-owner --no-acl |
gpg --batch --yes --quiet --encrypt --recipient "${GPG_RECIPIENT}" \
    --output "${DAILY_FILE}"

# 2. on day 1 of month, copy to monthly/
if [ "$(date -u +%d)" = "01" ] && [ ! -f "${MONTHLY_FILE}" ]; then
    cp -p "${DAILY_FILE}" "${MONTHLY_FILE}"
fi

# 3. verify size > 1 KB
SIZE="$(stat -c%s "${DAILY_FILE}" 2>/dev/null || stat -f%z "${DAILY_FILE}")"
if [ "${SIZE}" -lt 1024 ]; then
    echo "ERROR: backup file too small (${SIZE} bytes) — likely a dump failure" >&2
    exit 2
fi

# 4. verify GPG header (cheap sanity check; full integrity is decrypt-on-restore)
if ! gpg --list-only --quiet "${DAILY_FILE}" >/dev/null 2>&1; then
    echo "ERROR: GPG validation of ${DAILY_FILE} failed" >&2
    exit 3
fi

# 5. prune old archives
find "${BACKUP_DIR}/daily"   -type f -name "*.dump.gpg" -mtime +${RETENTION_DAYS} -delete
find "${BACKUP_DIR}/monthly" -type f -name "*.dump.gpg" -mtime +$((RETENTION_MONTHS * 31)) -delete

# 6. optional offsite rsync
if [ -f /root/.ssh/storagebox_key ] && [ -n "${STORAGEBOX_HOST:-}" ]; then
    rsync --quiet -az --delete \
        -e "ssh -i /root/.ssh/storagebox_key -o StrictHostKeyChecking=accept-new" \
        "${BACKUP_DIR}/" \
        "${STORAGEBOX_HOST}:goldrush-backups/"
fi

echo "[backup] OK ${DAILY_FILE} (${SIZE} bytes)" >&2
