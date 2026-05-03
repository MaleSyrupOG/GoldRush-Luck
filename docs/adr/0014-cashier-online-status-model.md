# ADR 0014 — Cashier online status is bot-state, not Discord presence

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

A cashier signs on for a shift via `/cashier-online` and signs off via `/cashier-offline`. The bot needs to track this so:

- The `#online-cashiers` embed updates with who is available right now (region + faction).
- A `/deposit` / `/withdraw` opener sees a non-empty roster and can be told "no cashiers in your region right now, expect a wait" if it's empty.
- The `claim_idle` worker can release a ticket if the cashier who claimed it has gone offline.

Two natural sources for "is the cashier online":

- **Discord Gateway presence** — the `Member.status` field (online / idle / dnd / offline) updated via the Presence Intent.
- **Bot-tracked state** — a `dw.cashier_status` row updated by the cashier's own slash commands.

## Decision

**Cashier online status is bot-tracked, not derived from Discord presence.** The source of truth is the `dw.cashier_status` table:

```
cashier_status (
    discord_id BIGINT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('online','offline','break')),
    state_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_action_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
)
```

Mutations only via SECURITY DEFINER fns (`dw.set_cashier_status`, `dw.expire_cashier`). Reads via the `cashier_roster` query that joins `dw.cashier_characters` on `discord_id` to bucket cashiers by region.

A background worker (`cashier_idle`, 5 min cadence) auto-transitions `online` → `offline` if `last_action_at` is more than `CASHIER_IDLE_TIMEOUT_SECONDS` (default 1 800 s = 30 min) old, emitting a `cashier_status_offline_expired` audit row.

## Reasons we chose bot-state over presence

1. **The Presence Intent is privileged.** Enabling it requires Discord verification once the bot crosses 100 guilds and changes the security review surface. Bot-tracked state needs zero privileged intents — `D/W` operates entirely on slash commands.

2. **Presence is noisy.** A cashier who minimises Discord shows as `idle`. A cashier with their phone in their pocket shows as `online` even when they're not paying attention. `online` in Discord ≠ "willing to take a ticket right now". Explicit `/cashier-online` makes the state intentional.

3. **Multi-account presence is ambiguous.** If a cashier is online from desktop AND mobile, Discord reports "online"; if their desktop logs out we briefly see the mobile presence — a noisy event we don't want driving the roster embed.

4. **A break is a meaningful state.** `online` / `offline` is binary in Discord; we needed a third state (`break`) so a cashier can signal "step away for 10 min, don't claim more tickets" without going fully offline. Bot-tracked state lets us model that.

5. **Audit-trail consistency.** Every cashier state transition writes an audit row. If the source were presence we'd have to either ingest presence events as audit rows (noisy) or have a hidden "internal status that mostly tracks presence". The bot-state model keeps audit-log meaning clean.

## Consequences

Positive:

- No privileged intents required. The bot's Discord application configuration is minimal.
- Cashier intent is explicit. The `/cashier-online` command is the audit anchor for "I started a shift".
- A `break` state for short pauses without losing the shift's context.
- The `cashier_idle` worker has a clean rule: auto-offline if no activity for 30 min, regardless of whether their Discord client is technically connected.

Negative:

- A cashier who closes Discord without `/cashier-offline` still shows online for up to 30 min (until the idle worker expires them). Tickets that target them via `claim_idle` get auto-released after 5 min. Documented in the runbook as a known visible delay.
- The roster is only as fresh as the cashier's most recent slash command. There is no real-time "left the chat" detection. Acceptable: tickets queue; cashiers self-claim; the system tolerates the delay.

## Alternatives considered

- **Presence Intent + bot-tracked state combined**: rejected because adding presence ingestion increases attack surface (privileged intent), audit-log noise, and code complexity for no behavioural improvement over the idle worker.
- **Presence-only**: rejected because of the multi-state, multi-device, intent-misalignment, and `break` issues above.

## References

- D/W design spec §6.6 (privileged intents — none)
- D/W design spec §5.7 (cashier session model)
- Migration `0009_dw_cashier_status.py` (table + SDFs)
- Migration `0016_dw_expire_cashier.py` (the auto-offline SDF)
- `deathroll_deposit_withdraw/cashiers/idle_worker.py`
