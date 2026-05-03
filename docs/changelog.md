# DeathRoll — changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
SemVer per bot. The D/W bot's tags are prefixed `dw-`; Luck and
Poker get `luck-` / `poker-` when their v1.0.0 ships.

---

## [dw-v1.0.0] — 2026-05-03

The Deposit/Withdraw bot v1 is feature-complete and production-
ready. Aleix is the sole author; built over the 5-day window
2026-04-29 → 2026-05-03 across 15 epics.

### What v1 ships

- **/deposit + /withdraw** end-to-end flows with private channel
  per ticket, modal-driven input with strict validation, fee
  capture at withdraw creation time (2 % default), balance lock +
  refund-on-cancel for withdraws.
- **Cashier system** — `/cashier-add-character` /
  `/cashier-online` / `/cashier-offline` / `/cashier-break` /
  `/cashier-mystats`. Region match enforced at claim time;
  multi-region cashiers supported.
- **Ticket lifecycle commands** — `/claim`, `/release`,
  `/confirm` (with 2FA `CONFIRM` magic word), `/cancel`,
  `/cancel-mine`.
- **Admin operational toolkit** — `/admin-setup`,
  `/admin-force-cashier-offline`, `/admin-cashier-stats`,
  `/admin-force-cancel-ticket`, `/admin-force-close-thread`.
- **Disputes & blacklist** — `/admin-dispute-{open,list,resolve,reject}`
  with edit-in-place embed in `#disputes`;
  `/admin-{ban,unban}-user` enforced at the SDF layer for both
  deposit and withdraw create flows.
- **Treasury (2FA-gated)** — `/admin-treasury-{balance,sweep,
  withdraw-to-user}` with 2-input and 3-input confirm modals
  requiring re-typed magic word + amount + recipient id.
- **Config edit** — `/admin-set-{deposit-limits,withdraw-limits,
  fee-withdraw}`, `/admin-set-{deposit,withdraw,cashier}-guide`
  via `EditDynamicEmbedModal`.
- **Audit log surface** — `/admin-view-audit` (paginated tail
  via `core.list_audit_events` SDF) and `/admin-verify-audit`
  (on-demand HMAC chain verification).
- **Background workers** — `ticket_timeout` (60 s),
  `claim_idle` (60 s), `cashier_idle` (5 min),
  `online_cashiers_embed_updater` (30 s),
  `stats_aggregator` (15 min), `audit_chain_verifier` (6 h),
  `metrics_refresher` (30 s).
- **Observability** — Prometheus exposition on port 9101 (10
  metric families per spec §7.3), Grafana dashboard JSON, 5
  Alertmanager rules with Discord webhook routing snippet.
- **Welcome embeds** — `#how-to-deposit`, `#how-to-withdraw`,
  `#cashier-onboarding` auto-seeded by the welcome reconciler;
  edit-in-place via the `set-*-guide` admin commands.
- **Audit log immutability** — `core.audit_log` rows are
  append-only at the trigger level; even `deathroll_admin` cannot
  UPDATE/DELETE. Hash chain verified periodically + on demand.

### Slash command total

38 slash commands at v1.0.0 ship.

### Migrations included

`0001_core_users_balances` … `0018_core_list_audit_events` (18
revisions). Run `alembic upgrade head` to apply on a fresh
database.

### Test coverage

- 472 unit tests
- 37 integration tests (testcontainers Postgres) — including the
  Story 15.2 50-user × 200-op stress test
- Suite total: 509 tests

mypy strict + ruff baseline clean.

### Security review

`docs/security-review-dw-2026-05-03.md` — APPROVED FOR LAUNCH.
22 SECURITY DEFINER functions reviewed; secret redaction model
verified; dispute audit trail complete; pip-audit clean modulo a
documented dev-only pytest advisory.

### Production deploy procedure

The operator follows `tests/reports/dw-smoke-2026-05-03.md`
(Story 15.1 checklist) against the staging guild before pushing
the `dw-v1.0.0` tag. The 48-hour watch window starts after
the tag lands.

Tag command (operator action — not auto-tagged by this commit):

```
git tag -a dw-v1.0.0 -m "DeathRoll Deposit/Withdraw v1.0.0"
git push origin dw-v1.0.0
```

### Repository

- GitHub: github.com/MaleSyrupOG/DeathRoll
- VPS: Hetzner sdr-agentic, container `deathroll-dw`

### Known accepted risks

- `EditDynamicEmbedInput.fields_json` tolerates malformed JSON
  (renders empty fields) — display-only payload, no money flows
  through. Pinned in
  `tests/unit/core/test_dw_pydantic_separators.py`.
- pytest 8.3.4 has GHSA-6w46-j5rx-g56g; fix is 9.0.3. Dev-only;
  pytest never runs in production. Bump deferred post-launch.

### Known follow-ups

- Story 14.8 (cross-bot integration tests) — gated on Luck
  resuming.
- Spec v1.1 bump documenting the "private channels not threads"
  design decision and `GRD-XXXX` UID format observed in the
  reference Discord (Aleix's pre-existing bot).
- `#alerts` Discord channel + Alertmanager Discord webhook —
  config snippet shipped at
  `ops/observability/alertmanager-discord.yml`; routing is on
  the operator to wire up.
