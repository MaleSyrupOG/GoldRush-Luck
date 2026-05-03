#!/usr/bin/env bash
# =============================================================================
# DeathRoll — create per-bot Postgres roles using passwords from environment.
#
# Runs ONCE on the first start of the deathroll-postgres container, after the
# main initdb finishes. The Postgres entrypoint sources environment variables
# from compose, so this script can read PG_LUCK_PASSWORD, PG_DW_PASSWORD,
# PG_POKER_PASSWORD, PG_READONLY_PASSWORD without exposing them on the
# process command line.
#
# Idempotent: every CREATE ROLE is wrapped in a guard.
# =============================================================================

set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${PG_LUCK_PASSWORD:?PG_LUCK_PASSWORD must be set}"
: "${PG_DW_PASSWORD:?PG_DW_PASSWORD must be set}"
: "${PG_READONLY_PASSWORD:?PG_READONLY_PASSWORD must be set}"
PG_POKER_PASSWORD="${PG_POKER_PASSWORD:-}"

create_role_if_missing () {
    local role="$1"
    local password="$2"

    if [ -z "$password" ] || [ "$password" = "disabled" ]; then
        echo "[init-roles] skipping ${role} (no password configured)" >&2
        return 0
    fi

    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" --variable=ON_ERROR_STOP=1 <<-SQL
        DO \$\$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '${role}') THEN
                EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '${role}', '${password}');
            ELSE
                EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '${role}', '${password}');
            END IF;
        END
        \$\$;
SQL
    echo "[init-roles] ensured role ${role}" >&2
}

create_role_if_missing deathroll_luck     "${PG_LUCK_PASSWORD}"
create_role_if_missing deathroll_dw       "${PG_DW_PASSWORD}"
create_role_if_missing deathroll_poker    "${PG_POKER_PASSWORD}"
create_role_if_missing deathroll_readonly "${PG_READONLY_PASSWORD}"

echo "[init-roles] done." >&2
