# DeathRoll Deposit/Withdraw — Final security review (Story 15.3)

**Date:** 2026-05-03
**Scope:** All D/W code merged on `main` at commit
`6f74671` (Story 15.2 close-out).
**Reviewer:** Aleix
**Sign-off:** ✅ APPROVED FOR LAUNCH (with one accepted-risk noted
in §5.2).

---

## 1. Dependency audit (`pip-audit`)

### Before remediation

```
Found 3 known vulnerabilities in 2 packages
Name   Version ID                  Fix Versions
------ ------- ------------------- ------------
pillow 11.0.0  GHSA-cfh3-3jmp-rvhc 12.1.1
pillow 11.0.0  GHSA-whj4-6x5x-4v2j 12.2.0
pytest 8.3.4   GHSA-6w46-j5rx-g56g 9.0.3
```

### After remediation

```
Found 1 known vulnerability in 1 package
Name   Version ID                  Fix Versions
------ ------- ------------------- ------------
pytest 8.3.4   GHSA-6w46-j5rx-g56g 9.0.3
```

### Action taken

- **Pillow 11.0.0 dropped** — was a leftover dependency for Luck's
  banner generator (paused). Zero current import touches it
  (`grep -r 'from PIL\|import PIL' goldrush_*` returns nothing).
  Removed `Pillow` and `types-Pillow` from `pyproject.toml`.
  Removes both CVE entries.
- **pytest 8.3.4 advisory documented as accepted risk** — see §5.2.

---

## 2. SECURITY DEFINER function audit

Every SDF was reviewed for: row-locking semantics, ``RAISE
EXCEPTION`` guards on illegal transitions, balance arithmetic, and
audit-log emission. Findings inline in this table.

| Migration | SDF | Audit row | Lock semantics | Verdict |
|---|---|---|---|---|
| 0006 | `create_deposit_ticket` | ✓ `deposit_ticket_opened` | none — no contended state | ✅ |
| 0006 | `confirm_deposit` | ✓ `deposit_confirmed` | `FOR UPDATE` on ticket + balance row | ✅ |
| 0006 | `cancel_deposit` | ✓ `deposit_cancelled` | `FOR UPDATE` on ticket | ✅ |
| 0007 | `create_withdraw_ticket` | ✓ `withdraw_ticket_opened` | `FOR UPDATE` on balance | ✅ |
| 0007 | `confirm_withdraw` | ✓ `withdraw_confirmed` + ✓ treasury `transfer_in` | `FOR UPDATE` on ticket + balance + treasury | ✅ |
| 0007 | `cancel_withdraw` | ✓ `withdraw_cancelled` | `FOR UPDATE` on ticket + balance | ✅ |
| 0008 | `claim_ticket` | ✓ `ticket_claimed` | `FOR UPDATE` on ticket | ✅ |
| 0008 | `release_ticket` | ✓ `ticket_released` | `FOR UPDATE` on ticket; `wrong_cashier` guard | ✅ |
| 0009 | `add_cashier_character` | ✓ | upsert | ✅ |
| 0009 | `remove_cashier_character` | ✓ | soft-delete | ✅ |
| 0009 | `set_cashier_status` | ✓ | `FOR UPDATE` on session row | ✅ |
| 0010 | `open_dispute` | ✓ `dispute_opened` | `UNIQUE (ticket_type, ticket_uid)` constraint | ✅ |
| 0010 | `resolve_dispute` | ✓ `dispute_resolved_<action>` | `FOR UPDATE` on dispute | ✅ |
| 0011 | `treasury_sweep` | ✓ `treasury_swept` | `FOR UPDATE` on treasury | ✅ |
| 0011 | `treasury_withdraw_to_user` | ✓ `treasury_withdraw_to_user` (both sides) | `FOR UPDATE` on treasury + target | ✅ |
| 0012 | `ban_user` | ✓ `user_banned` | upsert + `FOR UPDATE` | ✅ |
| 0012 | `unban_user` | ✓ `user_unbanned` | `UPDATE` direct (idempotent) | ✅ |
| 0013 | `reject_dispute` | ✓ `dispute_rejected` | `FOR UPDATE` on dispute | ✅ |
| 0015 | `create_deposit_ticket` (re-create) | ✓ — adds `banned` check at top | none new | ✅ |
| 0016 | `expire_cashier` | ✓ `cashier_status_offline_expired` | `FOR UPDATE` on cashier_status | ✅ |
| 0017 | `verify_audit_chain` | read-only | seq scan | ✅ |
| 0018 | `list_audit_events` | read-only | seq scan with `LIMIT` | ✅ |

**Money-moving operations: every single one writes an audit row.**
The hash chain (Story 8.6 verifier) re-validates this end-to-end.

**Privilege model**: all SDFs are `SECURITY DEFINER`, granted EXECUTE
to `deathroll_dw` only (with `core.list_audit_events` and
`core.verify_audit_chain` also on `deathroll_dw` because they live
in `core.*`). No table-level `INSERT`/`UPDATE`/`DELETE` is granted
to bot roles for `core.audit_log` (only `INSERT` for the chain
helper to do its work; trigger-level immutability blocks
`UPDATE`/`DELETE` even from admin — verified by integration test
`test_audit_log_update_rejected_by_trigger`).

---

## 3. Secret redaction

Every secret is typed `SecretStr` in
`deathroll_core/config/__init__.py`:

- `postgres_dsn`
- `button_signing_key`
- `audit_hash_chain_key`
- `discord_token`

`SecretStr` prevents accidental `repr` / `__str__` leaks across
structlog and pytest output. Secrets are unwrapped via
`.get_secret_value()` only at the consumption site:

- `client.py:131` — DSN passed to `asyncpg.create_pool`. Logging
  goes through `_redact_dsn` (`client.py:345`) which strips
  `user:pw@` and only logs the host+db.
- `__main__.py:61` — Discord token passed to `bot.start`. Never
  logged directly.

**Verified**: a fresh boot of the test container only logs
`{"event": "db_pool_ready", "dsn_host": "postgres:5432/deathroll"}`
— no password, no chain key.

---

## 4. Dispute resolution audit trail

Every dispute action writes an audit row:

| Verb | Audit action | Where |
|---|---|---|
| `/admin-dispute-open` | `dispute_opened` | migration 0010 fn `open_dispute` |
| `/admin-dispute-resolve` | `dispute_resolved_<action>` | migration 0010 fn `resolve_dispute` |
| `/admin-dispute-reject` | `dispute_rejected` | migration 0013 fn `reject_dispute` |
| Audit-log channel post | `audit_dispute_*` | `audit_log.py` event |

The `#disputes` channel embed (Story 9.2) edits a SINGLE
persisted message per dispute — the `dw.disputes` row carries
`discord_message_id` so resolve / reject EDIT in place rather
than posting a new message. The audit-log channel still receives
a transient note for every action, so admins have two views:
the long-lived dispute card in `#disputes`, the timeline in
`#audit-log`.

---

## 5. Known accepted risks

### 5.1. EditDynamicEmbedInput tolerates malformed JSON

**Where**: `deathroll_core/models/dw_pydantic.py` `EditDynamicEmbedInput`,
field `fields_json: str | None`.

**What**: malformed JSON in the `fields` payload of a dynamic embed
edit is NOT rejected at validation time. Downstream renderer
(`deathroll_core/embeds/dw_tickets.py::_parse_fields_json`) catches
the `JSONDecodeError` and renders an empty fields list.

**Why accepted**: a copy-paste typo in a guide-edit modal would
otherwise crash the live `#how-to-deposit` / `#how-to-withdraw`
embed rendering. Tolerance keeps the user-visible surface up.

**Risk**: zero. The `fields` are display-only metadata; nothing
budget-affecting flows through them.

**Test**: pinned in `tests/unit/core/test_dw_pydantic_separators.py`
(`test_edit_dynamic_embed_input_accepts_malformed_fields_json`)
so any future tightening surfaces as a deliberate change.

### 5.2. pytest 8.3.4 has GHSA-6w46-j5rx-g56g (dev-only)

**What**: pytest 8.3.4 has an advisory; fix is in 9.0.3.

**Why accepted**: pytest is in the dev dependency group. It
**never runs in production** — neither the Docker image nor any
runtime path touches it. The CVE surface is the developer's
local machine + CI runner.

**Why deferred**: pytest 9.x is a major version bump. Our pinned
`pytest-asyncio==0.25.0` was released against pytest 8.x and
hasn't been re-tested against pytest 9; chasing the bump
requires also bumping pytest-asyncio + verifying every async
test still works. Out of scope for the launch window.

**Mitigation**: scheduled bump as a follow-up issue post-launch.

---

## 6. Test posture at launch

- **472 unit tests** — `pytest -m "not integration"`, ~3.5 s
  wall-clock.
- **37 integration tests** — `pytest -m integration`, ~80 s with
  Docker-cold (testcontainers Postgres + 18 migrations + the
  Story 15.2 stress test).
- **mypy strict**: clean across 57 source files.
- **ruff**: 49 errors all pre-existing N818 (exception naming
  convention) + I001 (alembic migration import-block style); zero
  new regressions in this epic.

---

## 7. Sign-off

I, Aleix, having read the artefacts referenced above and confirmed
the test suite + dependency audit clean (with §5 noted as accepted
risks), authorise launch of `deathroll-dw` v1.0.0 to the live
DeathRoll guild.

**Date**: 2026-05-03
**Signature**: ✅ Aleix (`MaleSyrupOG`)
**Tag**: `dw-v1.0.0` (to be pushed when Story 15.4 closes).

---

## 8. Re-review trigger

Re-run this review when ANY of the following land:

- A new SECURITY DEFINER function (any migration starting `dw.` or
  `core.`).
- A change to the audit_log triggers or
  `core.audit_log_insert_with_chain`.
- A change to the role / grant matrix
  (`ops/postgres/01-schemas-grants.sql` or new role).
- A new secret added to `DwSettings` / `LuckSettings`.
- pip-audit output changes shape (new advisory in our deps).

Subsequent reviews append to this directory under
`docs/security-review-dw-YYYY-MM-DD.md`.
