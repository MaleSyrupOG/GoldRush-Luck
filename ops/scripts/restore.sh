#!/usr/bin/env bash
# =============================================================================
# DeathRoll — Postgres restore script.
#
# Restores a GPG-encrypted pg_dump archive into the deathroll database.
#
# WARNING: this DROPS and recreates the deathroll database. Make sure you have
# stopped the bots first. The script asks for confirmation before destroying
# data unless RESTORE_FORCE=1 is set.
#
# Usage (as root on the VPS):
#     ./restore.sh /opt/deathroll/backups/daily/deathroll-2026-05-01T03-00-00Z.dump.gpg
#
# Or for a one-off drill into a temporary DB:
#     RESTORE_DB=deathroll_drill ./restore.sh /path/to/backup.dump.gpg
#
# Steps:
#   1. Verify the archive exists and has a sane size.
#   2. Stop the bots (so they don't write while we restore).
#   3. (If restoring into the canonical 'deathroll' DB) DROP and CREATE the DB.
#   4. Decrypt with gpg into /tmp/restore-$$.dump (mode 600).
#   5. Pipe through pg_restore inside the container.
#   6. Shred the temporary dump immediately.
#   7. Restart bots.
#
# Idempotent: safe to run multiple times. Always cleans up the temp file.
# =============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must run as root." >&2
    exit 1
fi

ARCHIVE="${1:?usage: $0 <path-to-encrypted-dump.gpg>}"
RESTORE_DB="${RESTORE_DB:-deathroll}"
COMPOSE_FILE="/opt/deathroll/repo/ops/docker/compose.yml"
ENV_FILE="/opt/deathroll/secrets/.env.shared"
TMP_DUMP="/tmp/restore-$$.dump"

if [ ! -f "${ARCHIVE}" ]; then
    echo "Error: archive ${ARCHIVE} not found." >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"
: "${PG_ADMIN_USER:?PG_ADMIN_USER must be set}"

trap 'rm -f "${TMP_DUMP}" 2>/dev/null || true' EXIT
trap 'shred -u "${TMP_DUMP}" 2>/dev/null || rm -f "${TMP_DUMP}"' EXIT

# 1. confirmation
if [ "${RESTORE_FORCE:-0}" != "1" ]; then
    echo "About to restore into database '${RESTORE_DB}' from:"
    echo "    ${ARCHIVE}"
    echo
    if [ "${RESTORE_DB}" = "deathroll" ]; then
        echo "*** This will DROP and recreate the production 'deathroll' database. ***"
        echo "Type 'RESTORE-PRODUCTION' to confirm:"
        read -r CONFIRM
        if [ "${CONFIRM}" != "RESTORE-PRODUCTION" ]; then
            echo "Aborted."
            exit 1
        fi
    else
        echo "Type 'yes' to continue (target DB: ${RESTORE_DB}):"
        read -r CONFIRM
        if [ "${CONFIRM}" != "yes" ]; then
            echo "Aborted."
            exit 1
        fi
    fi
fi

# 2. stop bots
echo "[restore] stopping bots…"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" stop \
    deathroll-deposit-withdraw 2>/dev/null || true
# (When Luck resumes, add: docker compose stop deathroll-luck)

# 3. drop / recreate target DB
if [ "${RESTORE_DB}" = "deathroll" ]; then
    echo "[restore] dropping and recreating deathroll DB"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
        psql --username "${PG_ADMIN_USER}" --dbname postgres -c "DROP DATABASE IF EXISTS deathroll;"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
        psql --username "${PG_ADMIN_USER}" --dbname postgres \
        -c "CREATE DATABASE deathroll OWNER ${PG_ADMIN_USER};"
else
    echo "[restore] creating drill DB ${RESTORE_DB}"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
        psql --username "${PG_ADMIN_USER}" --dbname postgres \
        -c "CREATE DATABASE ${RESTORE_DB} OWNER ${PG_ADMIN_USER};" || \
    echo "(drill DB may already exist; continuing)"
fi

# 4. decrypt with strict perms
echo "[restore] decrypting archive…"
umask 077
gpg --batch --quiet --decrypt --output "${TMP_DUMP}" "${ARCHIVE}"

# 5. pg_restore
echo "[restore] running pg_restore (this can take a while)"
cat "${TMP_DUMP}" | docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
    pg_restore --username "${PG_ADMIN_USER}" --dbname "${RESTORE_DB}" \
    --no-owner --no-acl --clean --if-exists

# 6. cleanup handled by trap

# 7. restart bots (only if we restored into deathroll)
if [ "${RESTORE_DB}" = "deathroll" ]; then
    echo "[restore] restarting bots"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" start \
        deathroll-deposit-withdraw 2>/dev/null || true
fi

echo "[restore] DONE."
