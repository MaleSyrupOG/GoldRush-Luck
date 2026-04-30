-- =============================================================================
-- GoldRush — schemas and per-role grant matrix.
--
-- Runs ONCE after 00-init-roles.sh (alphabetical order). All role creation
-- has already happened; this file only deals with schemas and privileges,
-- so no environment variables are needed.
--
-- Idempotent: every CREATE uses IF NOT EXISTS. ALTER DEFAULT PRIVILEGES is
-- naturally idempotent.
-- =============================================================================

SET client_min_messages = WARNING;

-- =============================================================================
-- 1. Schemas
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS fairness;
CREATE SCHEMA IF NOT EXISTS luck;
CREATE SCHEMA IF NOT EXISTS dw;
CREATE SCHEMA IF NOT EXISTS poker;

-- =============================================================================
-- 2. Schema-level USAGE grants
-- =============================================================================

GRANT USAGE ON SCHEMA core, fairness, luck     TO goldrush_luck;
GRANT USAGE ON SCHEMA core, fairness, dw       TO goldrush_dw;
GRANT USAGE ON SCHEMA core, fairness, poker    TO goldrush_poker;
GRANT USAGE ON SCHEMA core, fairness, luck, dw, poker TO goldrush_readonly;

-- =============================================================================
-- 3. Default privileges for FUTURE tables (created by Alembic)
-- =============================================================================

-- goldrush_luck: full RW on luck and fairness; SELECT-only on core.* tables
-- (writes to core.balances and core.users are deliberately denied; only
-- goldrush_dw can mint/destroy balance outside SECURITY DEFINER functions).
ALTER DEFAULT PRIVILEGES IN SCHEMA luck
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_luck;
ALTER DEFAULT PRIVILEGES IN SCHEMA fairness
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_luck;
ALTER DEFAULT PRIVILEGES IN SCHEMA luck
    GRANT USAGE, SELECT ON SEQUENCES TO goldrush_luck;
ALTER DEFAULT PRIVILEGES IN SCHEMA fairness
    GRANT USAGE, SELECT ON SEQUENCES TO goldrush_luck;
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO goldrush_luck;
-- INSERT on core.audit_log granted explicitly per-table once Alembic creates it
-- (so we only allow INSERT, not full RW). See the dw default privileges below
-- for an analogous note.

-- goldrush_dw: the economic frontier. Full RW on dw, fairness; targeted
-- SELECT/INSERT/UPDATE on core.users and core.balances; INSERT on
-- core.audit_log only (writes are append-only by trigger as well).
ALTER DEFAULT PRIVILEGES IN SCHEMA dw
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_dw;
ALTER DEFAULT PRIVILEGES IN SCHEMA fairness
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_dw;
ALTER DEFAULT PRIVILEGES IN SCHEMA dw
    GRANT USAGE, SELECT ON SEQUENCES TO goldrush_dw;
-- core grants for goldrush_dw will be added per-table by the Alembic migration
-- that creates core.users / core.balances / core.audit_log (so the grants
-- match the spec: INSERT, UPDATE on core.users + core.balances; INSERT on
-- core.audit_log).

-- goldrush_poker: future bot. Mirrors goldrush_luck on its own poker.* schema.
ALTER DEFAULT PRIVILEGES IN SCHEMA poker
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_poker;
ALTER DEFAULT PRIVILEGES IN SCHEMA fairness
    GRANT SELECT, INSERT, UPDATE ON TABLES TO goldrush_poker;
ALTER DEFAULT PRIVILEGES IN SCHEMA poker
    GRANT USAGE, SELECT ON SEQUENCES TO goldrush_poker;
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO goldrush_poker;

-- goldrush_readonly: SELECT everywhere. Used by Grafana, by Aleix locally
-- via SSH tunnel for debugging, and by audit_verify.py.
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT ON TABLES TO goldrush_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA fairness
    GRANT SELECT ON TABLES TO goldrush_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA luck
    GRANT SELECT ON TABLES TO goldrush_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA dw
    GRANT SELECT ON TABLES TO goldrush_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA poker
    GRANT SELECT ON TABLES TO goldrush_readonly;

-- =============================================================================
-- 4. Audit log marker
-- =============================================================================
-- Future Alembic migrations add the actual tables. Once core.audit_log is
-- created, the migration also runs:
--     GRANT INSERT ON core.audit_log TO goldrush_luck, goldrush_dw, goldrush_poker;
-- because audit log writes are append-only and every bot must be able to log.

-- =============================================================================
-- Done. Alembic now owns all further schema evolution.
-- =============================================================================
