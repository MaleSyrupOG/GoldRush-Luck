# Disaster Recovery plan

> **Status**: stub (Story 1.5). Full content lands in Story 13.x (Operations final pass).

## RTO / RPO targets

TODO: Story 13.x — set explicit Recovery Time Objective and Recovery Point Objective. Suggested baseline: RTO 4 h, RPO 24 h (matching the nightly backup cadence).

## Failure modes

TODO: Story 13.x:
- VPS is destroyed (hardware loss / hosting account compromise)
- Postgres data corruption (logical bug, manual error)
- Audit-chain compromise (key leak)
- Discord token revoked
- DNS / domain hijack

## Recovery procedures

TODO: Story 13.x — for each failure mode above, a specific recovery procedure. The cross-cutting playbook lives in `runbook.md` §5; this doc captures the formal RTO/RPO decisions.

## Restore drill cadence

TODO: Story 13.x — quarterly restore drill into a staging environment; documented in `tests/reports/backup-restore-drill-YYYY-MM-DD.md`.

## References

- `backup-restore.md`
- `runbook.md` §5 (disaster recovery)
- `security.md` §6 (audit chain integrity)
