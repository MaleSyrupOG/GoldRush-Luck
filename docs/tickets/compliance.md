# Compliance and retention

This guide documents the bot's data-retention posture, the operator's responsibilities for financial-records compliance, and the immutability guarantees the bot offers.

> **Operator note**: this document is non-legal-advice. The bot is built to enforce technical guarantees (immutability, hash chain, audit trail). Whether the operator's particular jurisdiction requires longer retention, anonymisation, or different consent mechanisms is a question for the operator's lawyer.

---

## 1. What the bot stores

| Table | What | Retention default |
|---|---|---|
| `core.users` | Discord id + username (display) | Indefinite — never deleted |
| `core.balances` | Per-user gold balance | Indefinite |
| `core.audit_log` | Append-only event log; HMAC chain | Indefinite (technically immutable) |
| `dw.deposit_tickets` | Per-deposit history | Indefinite |
| `dw.withdraw_tickets` | Per-withdraw history | Indefinite |
| `dw.cashier_characters` | Cashier registered character names | Indefinite (soft-delete on `/cashier-remove-character`) |
| `dw.cashier_status` | Online/offline state | Indefinite |
| `dw.cashier_sessions` | Per-shift records | Indefinite |
| `dw.disputes` | Dispute records | Indefinite |
| `dw.blacklist` | Banned users | Indefinite |
| `dw.global_config` | Channel ids, fees, limits | Indefinite |

The default retention is "indefinite" because the operator's jurisdiction may require multi-year financial-record retention. The bot does not delete data on a timer.

---

## 2. The append-only audit log

`core.audit_log` is enforced as append-only at the database trigger level:

```sql
CREATE TRIGGER core.audit_log_immutable
BEFORE UPDATE OR DELETE ON core.audit_log
FOR EACH ROW
EXECUTE FUNCTION core.audit_log_block_mutation();
```

The trigger raises an exception on any UPDATE or DELETE. This applies to every Postgres role, including `deathroll_admin`. The only INSERT path is via `core.audit_log_insert_with_chain`, which is itself a SECURITY DEFINER fn that:

1. Reads the most recent `chain_link` value.
2. Computes `HMAC-SHA256(chain_key, prev_chain_link || new_row_payload)` using the `audit_hash_chain_key` from `.env.shared`.
3. Inserts the new row with the computed `chain_link`.

The chain is verified end-to-end by the `audit_chain_verifier` background worker (every 6h cadence) and on-demand via `/admin-verify-audit`. A break in the chain (an inserted row with the wrong link, an out-of-order insert) would surface as a `audit_chain_broken` log event and trigger an alert.

**This is the bot's "no row deletes" guarantee.** It survives role renames (Postgres preserves GRANTs by OID; the chain key in `.env.shared` survived the GoldRush → DeathRoll rename — verified empirically on 2026-05-03).

---

## 3. Backup and disaster recovery

A nightly `pg_dump` of the entire database is encrypted with the operator's GPG key (fingerprint stored off-VPS) and copied to an off-site location. Retention: 30 daily + 12 monthly + 5 yearly.

Restore drill: `tests/reports/backup-restore-drill-YYYY-MM-DD.md` documents a successful restore-from-backup-into-staging exercise. The current drill is overdue (no Story 12.6 completion record in `tests/reports/`); recommended cadence is quarterly.

If the production database is destroyed, the GPG-encrypted nightly dump is the recovery anchor. Worst-case data loss is one day.

---

## 4. Right-to-be-forgotten requests

Some jurisdictions allow a user to request their personal data be deleted. The bot's design makes this **partially incompatible** with the audit-log immutability:

- `core.users.username` could be replaced with a placeholder (`"<deleted user>"`); the discord_id is the join key and cannot change without breaking referential integrity.
- `core.balances` is the user's current balance; if the user wants to "withdraw and quit", a withdraw ticket goes through the normal flow and the row stays at `balance_g = 0`. Setting it to `NULL` or deleting it would break the treasury invariant query.
- `core.audit_log` rows referencing the user CANNOT be modified or deleted. The hash chain depends on every row remaining as-was.

The operator's options for a deletion request:

1. **Pseudonymise** — replace `core.users.username` with a placeholder; leave the discord_id (which is itself a numeric id, not a personal name). All audit-log rows continue to reference the user by id.
2. **Refuse** — cite the financial-records retention requirement that overrides the deletion request. This is jurisdiction-dependent.

The operator should document the policy in their privacy notice. The bot offers technical levers (pseudonymisation) but not full deletion.

---

## 5. Personally identifiable information (PII) inventory

The bot stores:

- **Discord user ids** (numeric snowflakes, unique per Discord account).
- **Discord usernames** (display strings as they appeared at the time of an audit row).
- **In-game character names** (registered by cashiers via `/cashier-add-character`; chosen by users in `/deposit` and `/withdraw` modals).
- **Discord channel ids and message ids** (referenced from the ticket and dispute tables).

The bot does NOT store:

- Real names or contact information.
- Email addresses.
- Payment details (the bot mediates WoW Gold, not real currency).
- Discord avatars or other profile metadata.

If the operator considers any of the above to be PII under their jurisdiction's law, treat the deletion request flow per §4.

---

## 6. Logging and observability

Structured logs (JSON) emitted by the bot at INFO level + above carry:

- `event` name (e.g., `deposit_confirmed`).
- `actor_id` (the discord_id of who triggered the event).
- `target_ref` (the ticket UID).
- `payload` (JSON, scrubbed of secrets).

Logs do NOT carry:

- `DISCORD_TOKEN`, `BUTTON_SIGNING_KEY`, `AUDIT_HASH_CHAIN_KEY` — typed `SecretStr` and redacted on `repr()`.
- The full `POSTGRES_DSN` — `_redact_dsn()` strips `user:pw@` before logging.
- Any password from any source.

The Prometheus exposition on `:9101/metrics` carries no PII — only counters, histograms, and gauges keyed by ticket type, region, status, and cashier_id (a numeric Discord id).

Log retention on the host: `docker-compose.yml` sets `max-size: "10m"` and `max-file: "5"` per container, so ~50 MB of rolling logs per service. Promtail (if configured by the operator) shipping to a remote log store extends this; the bot itself does not impose a retention period beyond the rolling docker logs.

---

## 7. Recommended retention windows

Suggested baseline (operator can adjust per jurisdiction):

| Data | Suggested retention | Rationale |
|---|---|---|
| `core.audit_log` (financial history) | 7 years | Financial-records compliance norms |
| `dw.*_tickets` | 7 years | Same |
| `dw.cashier_status`, `dw.cashier_sessions` | 7 years | Tied to per-confirm audit trail |
| `dw.global_config` | Indefinite | Configuration history; small volume |
| `dw.disputes` | 7 years | Investigation evidence |
| Container logs | 30 days | Operational debugging window |

These are operator-discretion. The bot does not enforce them; it accepts the operator's choice via no-deletion-from-trigger as the default.

---

## 8. Audit log forensics

For an investigation, the canonical query is:

```sql
SELECT id, action, actor_id, target_ref, payload, chain_link, created_at
FROM core.audit_log
WHERE
  target_ref LIKE 'GRD-%'  -- ticket-related
  AND created_at BETWEEN $1 AND $2
ORDER BY id ASC;
```

The hash chain is verifiable by the `core.verify_audit_chain` SDF or the published Python verifier in `verifier/python/`. A separately published verifier in `verifier/node/` allows a third party (e.g., a regulator or an auditor) to cryptographically verify the chain without trusting the operator's database access.

A non-malicious chain break is essentially impossible (the trigger forbids inserts that don't go through the chain helper, and the helper computes the link inside the same transaction). A malicious break would require Postgres superuser access; if you observe one, treat it as a compromise and rotate keys / restore from backup.

---

## 9. Compliance with Discord's Terms of Service

The bot is a Discord application. Discord's Developer Terms of Service apply:

- **No automated mass-DM or harassment**: the bot only responds to slash commands; no automated DMs.
- **No data resale**: the bot stores user-provided data for operational purposes only.
- **Compliance with privileged intents**: the bot does NOT request the Presence Intent or the Message Content Intent (privileged intents that require Discord verification at scale). All bot interactions are slash-command driven. See ADR 0014 and spec §6.6.

---

## 10. Compliance with WoW EULA

WoW Gold is a virtual asset whose terms of use are governed by Blizzard's End User Licence Agreement. The operator's responsibility:

- Aware that real-money sales of WoW Gold are a Blizzard EULA violation.
- The bot itself does NOT broker real-money transactions. It only mediates Gold-for-Gold (or Gold-for-bot-ledger) transfers.
- Gold-for-real-money would be a real-currency licensing question outside the bot's scope.

---

## 11. References

- ADR 0011 (D/W as economic frontier)
- ADR 0015 (treasury as system account)
- D/W design spec §6 (security model)
- D/W design spec §7.4 (backup and retention)
- D/W design spec §6.6 (privileged intents — none required)
- `docs/security.md` (the cross-cutting security posture)
- `docs/runbook.md` (incident response)
