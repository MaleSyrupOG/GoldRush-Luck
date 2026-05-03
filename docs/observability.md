# DeathRoll — Observability

The metric, log, and alert surfaces of the DeathRoll platform. D/W is the only bot in production today; Luck and Poker will add their own metric families when they ship.

---

## 1. Architecture

```
┌──────────────────┐                  ┌─────────────────┐
│ deathroll-dw     │  /metrics:9101   │ Prometheus      │
│ (the bot)        ├─────────────────▶│ scrape interval │
│                  │                  │ 15 s            │
│ structured JSON  │                  │                 │
│ logs to docker   │                  │  Alertmanager   │
└────────┬─────────┘                  │      ▲          │
         │                            │      │          │
         │   docker logs              └──────┴──────────┘
         ▼                                   │
   ┌─────────────┐                           │ webhook
   │ Promtail    │ → remote log store        ▼
   │ (operator)  │                    ┌──────────────┐
   └─────────────┘                    │ #alerts in   │
                                      │ Discord      │
                                      └──────────────┘
```

Prometheus runs outside `deathroll_net` (operator's monitoring stack). The bot exposes `/metrics` on port 9101 inside the network only; for an external scrape, the operator opts in via `compose.observability.yml` (see ADR equivalent in commit history).

---

## 2. Metric families

D/W exposes 10 families on `:9101/metrics`. All are prefixed `deathroll_`.

| Family | Type | Labels | Description |
|---|---|---|---|
| `deathroll_deposit_tickets` | Gauge | `status` | Total deposit tickets by status. DB aggregate, refreshed every 30 s by the `metrics_refresher` worker. |
| `deathroll_withdraw_tickets` | Gauge | `status` | Same for withdraw. |
| `deathroll_deposit_volume_g` | Counter | `region` | Cumulative confirmed deposit gold by region. Increments at every `deposit_confirmed` audit row. |
| `deathroll_withdraw_volume_g` | Counter | `region` | Same for withdraw. |
| `deathroll_treasury_balance_g` | Gauge | (none) | Bot-tracked treasury balance (`core.balances WHERE discord_id=0`). |
| `deathroll_cashiers_online` | Gauge | `region` | Distinct cashiers in `state='online'` by region of their characters. |
| `deathroll_ticket_claim_duration_s` | Histogram | `ticket_type` | Time from cashier claim to confirm, in seconds. |
| `deathroll_ticket_confirm_duration_s` | Histogram | `ticket_type` | Latency of the `dw.confirm_*` SECURITY DEFINER call (from cog timing). |
| `deathroll_cashier_dispute_rate` | Gauge | `cashier_id` | Disputes per cashier as a fraction of their confirmed tickets. |
| `deathroll_fee_revenue_g` | Counter | (none) | Cumulative fee revenue (sum of withdraw fees on confirmed tickets). |

The `metrics_refresher` worker runs every 30 s and re-queries the gauges from Postgres. The counters increment in real time as audit rows are inserted (via the cog-side hook in `metrics.py`).

---

## 3. Alertmanager rules

Five rules ship with the v1.0.0 launch (`ops/observability/alerts/deathroll-dw.yml`). Each routes to `#alerts` via the Discord webhook configured by the operator.

| Alert | Severity | Condition | Why it matters |
|---|---|---|---|
| `DeathRollDWStuckTicket` | warning | `max(deathroll_ticket_claim_duration_s_count{ticket_type=~".+"}) by (ticket_type) - prev: > 0 for 2h` (any single ticket in `claimed` for > 2 h) | Cashier abandoned a claim; users are stuck waiting |
| `DeathRollNoCashiersOnline` | warning | `deathroll_cashiers_online == 0 for 10 min AND tickets opening` | Users opening tickets with zero cashiers — bad UX, possibly all cashiers logged off without telling anyone |
| `DeathRollTreasuryDrop` | high | `deathroll_treasury_balance_g[1h] decreased > 1 000 000` | Sudden treasury drop — investigate dispute refunds or admin sweeps |
| `DeathRollHighCancellationRate` | warning | `rate(deathroll_*_tickets{status=~"cancelled"}[1h]) > 0.20` | More than 20 % of tickets cancelled in the last hour — UX issue or bot-fraud signal |
| `DeathRollUnusualCashierActivity` | low | `rate(deathroll_*_tickets{status=~"confirmed"}[5min]) per cashier > 3 stddev` | A cashier confirming abnormally fast — may be legitimate (peak hours) but worth a glance |

The webhook YAML lives at `ops/observability/alertmanager-discord.yml` as a template. The operator writes the actual webhook URL into their Alertmanager configuration; the bot does NOT have access to the webhook URL.

---

## 4. Log structure

Every log line is JSON, one event per line:

```json
{
  "event": "deposit_confirmed",
  "level": "info",
  "actor_id": 123456789,
  "ticket_uid": "GRD-A1B2",
  "amount": 50000,
  "region": "EU",
  "timestamp": "2026-05-03T20:08:14.523Z",
  "logger": "deathroll_deposit_withdraw.cogs.ticket"
}
```

Standard fields: `event`, `level`, `timestamp`, `logger`. Event-specific fields in payload.

Redaction happens at the field-level via `structlog`:

- `dsn` → host/db only (the `_redact_dsn` processor)
- `discord_token`, `chain_key`, `signing_key` → never appear in logs (typed `SecretStr`; would emit as `<SecretStr>` if mistakenly added to a payload)

A clean boot of `deathroll-dw` emits ~25 lines, all at `info` level. A line at `warning` or `error` level is a real signal worth investigating.

### 4.1. Reading logs

```bash
docker logs deathroll-dw --tail 100 | jq .              # pretty-print all
docker logs deathroll-dw --tail 1000 | jq 'select(.level=="error")'   # errors only
docker logs deathroll-dw --tail 1000 | jq 'select(.event=="deposit_confirmed")'  # by event type
```

### 4.2. Long-term log storage

The operator's choice:

- **Promtail → Loki / Grafana** — configured externally; the docker `logging_jobname` label (`deathroll-dw`) is the join key.
- **journald + remote forwarder** — alternative if the operator prefers system logs.
- **Plain docker rolling logs** (the default in `compose.yml`: `max-size: 10m`, `max-file: 5`) — ~50 MB rolling, no off-site shipping.

For incident forensics, the bot's `core.audit_log` is more useful than the docker logs: every gold-moving event has a row in the audit log, indexed and queryable.

---

## 5. Grafana dashboard

A starter dashboard JSON ships at `ops/observability/grafana-dashboards/deathroll-dw.json`. It includes panels for:

- Active tickets by state (deposit + withdraw, stacked).
- Cumulative confirmed volume (deposit, withdraw, by region).
- Treasury balance over time.
- Cashiers online (current + 7-day trend).
- Claim → confirm latency histogram (P50 / P95 / P99).
- Confirm SDF latency.
- Fee revenue over time.
- Dispute count + dispute rate per cashier.

The operator imports the JSON into their Grafana instance and points it at the Prometheus data source.

---

## 6. Tracing (deferred)

v1.0.0 does NOT ship distributed tracing. The asyncpg + discord.py call stack is shallow enough that structured logs + the `*_duration_s` histograms cover the latency-attribution use cases.

If a future bot has a deeper call graph (e.g., Poker with multi-player table state), an OpenTelemetry tracing surface would land alongside.

---

## 7. The audit chain re-verifier

A first-class "did we lose data integrity" signal:

- The `audit_chain_verifier` background worker walks the chain every 6 h. Emits `audit_chain_verified` (good) or `audit_chain_broken` (bad).
- The `/admin-verify-audit` slash command runs an on-demand re-walk; renders an embed with the result.
- The post-rename smoke check on 2026-05-03 used this exact event as proof of a clean cutover (`last_verified_id: 17`).

A `audit_chain_broken` event should fire an Alertmanager rule (see runbook §3.3 for the response procedure). v1.0.0 ships the worker but does NOT yet ship the Alertmanager rule that would catch it — added as a follow-up TODO.

---

## 8. Health endpoint

The bot does NOT expose an HTTP health endpoint. The Docker healthcheck is the canonical "is the bot alive?" signal:

```yaml
healthcheck:
  test: ["CMD", "python", "-m", "deathroll_deposit_withdraw.healthcheck"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s
```

The healthcheck script opens an asyncpg pool with a 3 s timeout, runs `SELECT 1`, exits 0 only on the exact result. Any other state (timeout, exception, wrong value) exits 1. Docker's `unless-stopped` restart policy then handles the rest.

For external "is the bot alive" probes, the operator can:

- Use Docker socket access (the same as `docker ps --filter health=unhealthy`).
- Scrape `:9101/metrics`; the endpoint serving 200 means the bot has started.
- Check the audit-log table: a successful boot writes a `bot_started` audit row.

---

## 9. References

- D/W design spec §7.3 (observability additions)
- ADR 0014 (cashier online — bot-state, not presence; explains why `deathroll_cashiers_online` is reliable)
- `runbook.md` — incident playbooks that reference the alerts
- `security.md` — security pillars including monitoring
- `ops/observability/alerts/deathroll-dw.yml` — Alertmanager rules
- `ops/observability/alertmanager-discord.yml` — Discord webhook template
- `ops/observability/grafana-dashboards/deathroll-dw.json` — Grafana panels
