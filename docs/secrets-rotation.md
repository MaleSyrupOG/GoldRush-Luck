# Secrets rotation

> **Status**: stub (Story 1.5). Full content lands in Story 13.x.

## Inventory of secrets

TODO: Story 13.x — full inventory. Initial list:

| Secret | Where | Rotation cadence (recommended) | Procedure |
|---|---|---|---|
| `PG_ADMIN_PASSWORD` | `.env.shared` | annually | TODO |
| `PG_LUCK_PASSWORD`, `PG_DW_PASSWORD`, `PG_POKER_PASSWORD`, `PG_READONLY_PASSWORD` | `.env.shared` | annually | TODO |
| `BUTTON_SIGNING_KEY` | `.env.shared` | annually | TODO |
| `AUDIT_HASH_CHAIN_KEY` | `.env.shared` | **never under normal operation** — rotation invalidates the existing chain | TODO (chain re-anchor procedure) |
| `DISCORD_TOKEN` (per bot) | `.env.<bot>` | on incident only | TODO |
| GPG private key for backups | `/opt/deathroll/secrets/backup-gpg-private.asc` | annually | TODO |

## Rotation procedure (general shape)

TODO: Story 13.x.

1. Generate the new secret outside the VPS (laptop with offline Python/openssl).
2. Stage the new value into `.env.shared` alongside the old (for keys that allow dual-acceptance).
3. Re-deploy.
4. Verify the new secret works end-to-end.
5. Remove the old value.
6. Document the rotation in an audit-log entry.

## The audit hash chain key — special case

TODO: Story 13.x — rotating the chain key invalidates all existing chain verification. Procedure: stop the bot; re-walk the chain re-computing every `chain_link` with the new key; the new chain becomes the canonical record from that point. The original chain is preserved in a backup snapshot for forensic purposes.

## References

- `security.md` §5 (secret redaction model)
- `compliance.md` §8 (audit forensics)
