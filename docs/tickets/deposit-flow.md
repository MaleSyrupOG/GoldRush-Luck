# Deposit flow

How to send WoW Gold from your character into your DeathRoll balance, end-to-end.

> **TL;DR**: in `#deposit`, run `/deposit`. A private channel `#deposit-grd-XXXX` opens with you, the cashier, and admins. A cashier comes online, claims the ticket, you trade them in-game, the cashier confirms, your balance updates.

---

## 1. Prerequisites

Before you start, make sure of all four:

- You're a member of the DeathRoll guild on Discord.
- You have a WoW character on **EU** or **NA**, **Alliance** or **Horde**.
- You have at least **1 000 G** in that character's bag (the per-deposit minimum at v1.0.0; see `/admin-set-deposit-limits` for the live value).
- You're not banned from D/W (see `disputes.md` if you think you've been banned in error).

---

## 2. Run `/deposit`

In the `#deposit` channel, type `/deposit` and press Enter. A modal pops up with four fields:

| Field | What to type | Example |
|---|---|---|
| Amount | The number of gold pieces — digits only, no commas, no `k`/`m` suffix | `25000` |
| Character | Your in-game character name | `Aelara` |
| Region | `EU` or `NA` (case-sensitive) | `EU` |
| Faction | `Alliance` or `Horde` (case-sensitive) | `Alliance` |

Hit Submit.

> **Tip**: amounts cannot use suffixes. `25k` is not accepted; type `25000`. The bot rejects suffixed amounts deliberately to avoid the "I meant 25k = 25 000" vs "no, 25k = 250 000" ambiguity.

If anything is invalid (region misspelled, amount below the minimum, character name too long, etc.), you get an ephemeral error embed naming the bad field. Re-run `/deposit` to try again.

---

## 3. The private ticket opens

On a successful submit, the bot:

1. Creates a private channel under the `Banking` category. Name format: `deposit-grd-<4-char>` — e.g., `#deposit-grd-a1b2`. The 4-char part is the canonical UID slug.
2. Adds you, the `@cashier` role, and the `@admin` role to the channel.
3. Posts a `Deposit ticket — submitted` embed inside, showing the amount, character, region, faction, and the unique ID `GRD-A1B2`.
4. Pings `@cashier` (with a region/faction filter visible in the alert) in `#cashier-alerts`.

You'll get a Discord notification taking you straight to the new channel. The original `/deposit` command shows you an ephemeral confirmation with the channel link.

---

## 4. A cashier claims the ticket

Cashiers see your alert in `#cashier-alerts`. The alert lists which cashiers are currently online and compatible with your region/faction. One of them runs `/claim` inside your private channel.

Once claimed:

- The ticket embed updates to "Awaiting cashier" → then "Claimed" with the cashier's name and their compatible character.
- The cashier writes their in-game character name + location in the channel: e.g., `"Meet me at Stormwind Auction House. Char: Goldhand."`.
- Your job at this point: log into your character on the right region/faction, find the cashier, hit them up for a trade.

> **What if no cashier comes online?** v1.0.0 has no automatic compensation for slow claims. The `/admin-cashier-stats` table tracks claim latency and admins page cashiers manually if a ticket sits unclaimed for a long time. If your ticket has been waiting > 1 hour, it's reasonable to ping `@admin` once in your private channel.

---

## 5. The trade

In-game, you trade the cashier the agreed amount of gold. **Trade exactly the amount in the ticket.** If you trade 25 100 instead of 25 000, the cashier will ask you to either:

- Trade back the 100 (clean), or
- Cancel the ticket and reopen with the corrected amount (slower but clearer audit-log trail).

Do NOT trade extra "to round up". Audit-log integrity depends on tickets matching the exact amount confirmed.

---

## 6. The cashier confirms

After the in-game trade is complete, the cashier types `/confirm` in your private channel. A 2FA modal pops up asking the cashier to type `CONFIRM`. Once they do:

- Your balance increases by the deposited amount, atomically inside the SECURITY DEFINER `dw.confirm_deposit` transaction.
- The ticket embed updates to "Confirmed", with your new balance shown.
- An audit-log row `deposit_confirmed` is written in `core.audit_log`. The HMAC chain links it to every prior audit row.

You now have the gold in your DeathRoll balance. Run `/balance` anywhere to check.

---

## 7. Closing the channel

After confirm, the channel is no longer needed for active conversation. An admin will run `/admin-force-close-thread` on it (see `ticket-lifecycle.md` — the command archives the channel). The channel stays in the guild as a permanent paper trail; you can scroll back to see the audit history if you ever need to.

If a dispute arises later (you say you sent more than was confirmed, the cashier disagrees), see `disputes.md`.

---

## 8. Worked example with realistic numbers

You want to deposit **300 000 G** to your DeathRoll balance for a Saturday-night raffle.

1. **Tuesday 21:14 UTC** — In `#deposit`, you run `/deposit`. Modal: `300000` / `Maeve` / `EU` / `Alliance`. Submit.
2. **Tuesday 21:14 UTC** — Channel `#deposit-grd-7c8f` opens. Embed shows the ticket.
3. **Tuesday 21:15 UTC** — Cashier `Bandit` claims. They write: `"Meeting you at SW AH. Char: Goldfinger. EU-Alliance."`.
4. **Tuesday 21:18 UTC** — You log onto Maeve, find Goldfinger, trade 300 000 G.
5. **Tuesday 21:19 UTC** — Bandit runs `/confirm`, types `CONFIRM` in the modal. Your balance jumps from 0 to 300 000 G. Audit row `deposit_confirmed` written.
6. **Tuesday 21:20 UTC** — Admin `Aleix` archives the channel. The paper trail stays.

Total elapsed: ~6 minutes. Typical for a single online cashier.

---

## 9. Cancelling a ticket

You can cancel your own ticket while it is still in state `open` or `claimed` (i.e., before `confirm`):

```
/cancel-mine ticket:GRD-7C8F
```

This calls `dw.cancel_deposit` and writes a `deposit_cancelled` audit row. The cashier (if there was one) is notified. The channel can then be archived.

A cashier or admin can also cancel via `/cancel` (cashier-side, only their own claims) or `/admin-force-cancel-ticket` (admin-side, any ticket, with a free-text reason).

> **You cannot cancel a confirmed ticket.** Once confirmed, the gold has moved. To dispute a confirmed ticket see `disputes.md`.

---

## 10. Troubleshooting

| Symptom | What to check |
|---|---|
| `/deposit` says "you are banned" | See `disputes.md` for unban procedure |
| `/deposit` says "amount above limit" | The per-ticket maximum is set by `/admin-set-deposit-limits`; check `#how-to-deposit` for current values |
| Modal won't submit | Check region/faction case (`EU` not `eu`); check the amount is a plain integer |
| Channel never opens | Check `#deposit` for an ephemeral error reply; if missing, try once more — Discord modals can rarely fail with no error |
| No cashier claims for hours | Ping `@admin` once in your private channel; admins manually wake cashiers |
| Cashier doesn't respond after claim | After 30 min of cashier inactivity the auto-release worker un-claims; another cashier can pick it up |

---

## 11. References

- `withdraw-flow.md` — the symmetric flow for taking gold out
- `ticket-lifecycle.md` — the formal state machine (open → claimed → confirmed/cancelled)
- `disputes.md` — what to do when something went wrong post-confirm
- D/W design spec §5.1 (deposit flow)
