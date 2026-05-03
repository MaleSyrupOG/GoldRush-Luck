# Treasury management (admin guide)

The treasury holds withdraw fees collected by the bot. This guide is for admins who need to inspect, sweep, or refund from the treasury.

> **TL;DR**: the treasury is a `core.balances` row at `discord_id = 0`. It increases by `fee` on every confirmed withdraw. Admins reduce it via `/admin-treasury-sweep` (records out-of-bot transfer to the operator) or `/admin-treasury-withdraw-to-user` (refunds, dispute resolutions, prizes). Both require a 2FA modal.

---

## 1. Where the treasury lives

The treasury is **not a separate table**. It is a single row in `core.balances`:

```sql
SELECT * FROM core.balances WHERE discord_id = 0;
```

The corresponding `core.users` row at `discord_id = 0` carries `username = '__treasury__'`. Discord snowflake ids start at the Discord epoch and cannot be `0`, so this id is a safe sentinel.

See ADR 0015 for the design reasoning.

---

## 2. Reading the treasury balance

```
/admin-treasury-balance
```

This calls the `dw.get_treasury_balance()` SDF (read-only) and renders an embed with:

- Current balance (gold).
- Last-updated timestamp.
- Last sweep amount + timestamp.
- Last `withdraw-to-user` amount + recipient + timestamp.

The embed is admin-only. Cashiers cannot read the treasury.

---

## 3. How the treasury grows

Every time a cashier runs `/confirm` on a withdraw ticket, the SECURITY DEFINER fn `dw.confirm_withdraw` writes a `transfer_in` audit row crediting `discord_id = 0` with the fee. The fee was already debited from the user at create-time (see `withdraw-flow.md` §2 and ADR 0011); the audit row at confirm-time documents the transfer.

If a withdraw is cancelled before confirm, `dw.cancel_withdraw` issues a refund (`transfer_out` from treasury back to the user) and the treasury reverts.

The net invariant: the treasury balance always equals the sum of confirmed-withdraw fees, minus the sum of admin sweeps and admin withdrawals-to-user. Pinned in the property test `test_treasury_invariant_holds_under_concurrency`.

---

## 4. Sweeping the treasury

When the treasury is large enough that the operator wants to move the gold to an in-game guild bank (out of the bot's accounting), they run:

```
/admin-treasury-sweep amount:<integer>
```

This opens a `TwoInputConfirmModal`:

- Field 1: `Type CONFIRM to commit`.
- Field 2: `Re-type the amount`.

On match (case-sensitive `CONFIRM` + integer-equal amount), `dw.treasury_sweep` runs:

1. Locks the `core.balances` row at `discord_id = 0` `FOR UPDATE`.
2. Verifies `balance_g >= amount`. If not, raises `InsufficientFunds`.
3. Debits treasury by `amount`.
4. Inserts a `treasury_swept` audit row with the actor (admin's discord_id), amount, and timestamp.

After the SDF commits:

- Webhook alert posts to `#alerts` with `"Treasury swept: -<amount> by @<admin>"`.
- The treasury balance decreases by `amount`.

> **The "swept" gold goes outside the bot.** The operator is then responsible for moving the corresponding amount in-game from the cashiers' shared account to the operator's guild bank. This step is manual and tracked in the operator's external records, NOT in the bot's audit log.

### Worked example

Treasury balance before: `1 234 000 G`.

Admin runs `/admin-treasury-sweep amount:1000000`. Modal: types `CONFIRM`, re-types `1000000`. Submit.

```
audit_log row 4218: treasury_swept
  actor_id   = <admin discord_id>
  payload    = {"amount": 1000000, "balance_before": 1234000, "balance_after": 234000}
  created_at = 2026-05-08 14:23:01 UTC
  hash_link  = (link to row 4217 chained via HMAC-SHA256)
```

`#alerts` channel: `[Treasury] -1,000,000 G swept by @Aleix. Remaining: 234,000 G.`

---

## 5. Withdrawing from treasury to a user

For dispute refunds, jackpot prizes, or operator-driven gifts:

```
/admin-treasury-withdraw-to-user user:@<u> amount:<integer> reason:<text>
```

This opens a `ThreeInputConfirmModal`:

- Field 1: `Type CONFIRM`.
- Field 2: `Re-type the amount`.
- Field 3: `Re-type the recipient discord id`.

The 3rd input requires the admin to literally retype the user's full Discord id (e.g., `1234567890123456789`). This is the strongest UX guard against pasting the wrong recipient.

On all-three-match, `dw.treasury_withdraw_to_user` runs:

1. Locks `core.balances` at `discord_id = 0` AND at the target.
2. Verifies treasury balance ≥ amount. Raises if not.
3. Debits treasury by `amount`.
4. Credits target user by `amount`.
5. Writes TWO audit rows: `treasury_withdraw_to_user` (treasury side) and `transfer_in` (user side), linked by a shared `correlation_id` in the payload.
6. Webhook alert: `"Treasury → user: <amount> to @<u> by @<admin>: <reason>"`.

### Worked example

User `Aelara` reports a confirmed withdraw was short-changed by the cashier (50 000 G missing). After admin investigation (see `disputes.md`), refund decision is yes-50k.

Admin runs `/admin-treasury-withdraw-to-user user:@Aelara amount:50000 reason:"Dispute GRD-A1B2 — cashier short-traded; admin refund."`. Modal: `CONFIRM` / `50000` / `<aelara's discord id>`. Submit.

```
audit_log row 4219: treasury_withdraw_to_user
  actor_id   = <admin discord_id>
  target_ref = "USER:<aelara discord_id>"
  payload    = {"amount": 50000, "reason": "...", "correlation_id": "<uuid>"}
audit_log row 4220: transfer_in
  actor_id   = 0  (treasury as actor)
  target_ref = "USER:<aelara discord_id>"
  payload    = {"amount": 50000, "source": "treasury", "correlation_id": "<uuid>"}
```

Both rows share the same `correlation_id` so they pair in audit queries.

---

## 6. The 2FA modal pattern

Both treasury operations use the multi-input confirm modal pattern (ADR 0016):

| Operation | Inputs | Why this many |
|---|---|---|
| `/admin-treasury-sweep` | `CONFIRM` + amount | Two: re-typing the amount catches off-by-zeroes mistakes |
| `/admin-treasury-withdraw-to-user` | `CONFIRM` + amount + recipient | Three: also catches mis-paste of the recipient id |
| Other admin commands | `CONFIRM` + (operation-specific) | Operation-specific magic words: `BAN`, `FORCE-CANCEL` |

The modals are single-shot. If you mistype, the bot replies with an ephemeral error naming the bad field; the SDF is NOT called. Re-run the slash command to start fresh.

---

## 7. Reading the treasury history

```
/admin-view-audit user:@__treasury__
```

There is no real Discord user named `__treasury__`, but the `/admin-view-audit` command supports `user:0` as a fallback alias for the treasury. The output is paginated (50 rows per page) with action + amount + actor + timestamp.

For a SQL-side view:

```sql
SELECT id, action, actor_id, target_ref, payload, created_at
FROM core.audit_log
WHERE
  action IN ('transfer_in', 'transfer_out', 'treasury_swept', 'treasury_withdraw_to_user')
  AND (
    target_ref = 'USER:0'
    OR (action = 'transfer_in' AND payload->>'source' = 'treasury')
  )
ORDER BY id ASC;
```

This returns every row that touches the treasury balance.

---

## 8. The Alertmanager rules

Two of the five Alertmanager rules (Story 11.x) directly target the treasury:

| Alert | Condition | Action |
|---|---|---|
| `DeathRollTreasuryDrop` | Treasury drops > 1 M G in 1h | Discord webhook to `#alerts`; admins inspect the audit log |
| `DeathRollTreasuryHighBalance` | Treasury exceeds operator-configured threshold | Indicates "time to schedule a sweep" |

These rules fire from the Prometheus exposition on port 9101; the routing is on the operator (`ops/observability/alertmanager-discord.yml` snippet documents the webhook setup).

---

## 9. The retention question

`core.audit_log` rows that pertain to treasury operations may be subject to financial-records retention requirements depending on the operator's jurisdiction. The bot does NOT delete audit rows automatically; the trigger-driven append-only constraint forbids it. See `compliance.md` for retention guidance.

---

## 10. References

- `disputes.md` — the workflow for which treasury withdraw-to-user is the resolution
- `compliance.md` — retention and forensic requirements
- ADR 0011 (D/W as economic frontier)
- ADR 0015 (treasury as system account)
- ADR 0016 (2FA modals for money operations)
- D/W design spec §6.2 (treasury operations are super-restricted)
- Migration `0011_dw_treasury.py` (the SDFs)
