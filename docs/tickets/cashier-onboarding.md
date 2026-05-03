# Cashier onboarding

This is the canonical guide for new cashiers. It is linked from the `#cashier-onboarding` welcome embed inside the guild.

> **TL;DR**: register your characters with `/cashier-add-character`. When you start a shift, run `/cashier-online`. Watch `#cashier-alerts` for tickets in your region/faction. Claim with `/claim` inside the ticket channel, trade in-game, then `/confirm`. Sign off with `/cashier-offline` when done.

---

## 1. Who can be a cashier

You need the `@cashier` Discord role. Admins assign this manually via Server Settings → Members. The bot does NOT grant or revoke roles — the spec §6.5 explicitly forbids `Manage Roles` for the bot.

If you've been given the `@cashier` role, you can use the `/cashier-*` slash commands. If you haven't, the bot will reply with "you don't have permission".

---

## 2. Register your characters

Each character you cash with must be registered. The bot uses the character list to:

- Check region/faction compatibility when you try to claim a ticket.
- Render your name + character in the cashier-alert embed.

```
/cashier-add-character character:Goldhand region:EU faction:Horde
```

The bot validates that:

- `region` is `EU` or `NA` (case-sensitive).
- `faction` is `Alliance` or `Horde` (case-sensitive).
- The character name is not already registered to another cashier (uniqueness).

You can register multiple characters across regions and factions; multi-region cashiers are explicitly supported. Each `/cashier-add-character` writes an audit row.

```
/cashier-list-characters
```
Lists everything you've registered.

```
/cashier-remove-character character:Goldhand
```
Soft-deletes a character (sets the `removed_at` timestamp). The character cannot be re-used by another cashier; if you need to re-add it, contact an admin.

---

## 3. Going online

When you start a shift, run:

```
/cashier-online
```

This:

- Inserts a row in `dw.cashier_status` with `state='online'` and `state_changed_at = NOW()`.
- Adds you to the `#online-cashiers` embed (auto-refreshed every 30 s).
- Makes you eligible to claim tickets in your region(s)/faction(s).

The `/cashier-online` slash command must be run inside `#cashier-onboarding` (enforced by the bot — running it elsewhere returns a "wrong channel" error). This is the audit anchor for "I started a shift in the right place at the right time".

> **The bot does NOT detect your Discord presence.** If you close Discord without running `/cashier-offline`, you'll show as online for up to 30 minutes, then the `cashier_idle` background worker auto-transitions you to offline. See ADR 0014 for the reasoning.

---

## 4. Claiming a ticket

A user runs `/deposit` or `/withdraw`. A ticket alert appears in `#cashier-alerts` with their region + faction. If you're online and have a character matching their region/faction, you can claim.

To claim, go INSIDE the user's private ticket channel and run:

```
/claim
```

The SDF `dw.claim_ticket` validates:

- The ticket exists and is in state `open`.
- You have `state='online'` (not `offline`, not `break`).
- You have at least one registered character matching the ticket's region+faction.

On success: the ticket transitions to `claimed`, the embed updates to show your username and the matching character. An audit row `ticket_claimed` is written.

> **A ticket can only have one claimer at a time.** If two cashiers race for the same ticket, PostgreSQL's `FOR UPDATE` lock makes the second one fail with `AlreadyClaimed`. There is no merge logic — the second cashier moves on to a different ticket.

---

## 5. Posting your in-game info

The expected first message after claiming is your in-game character name + meeting location, e.g.:

> Meet me at the Stormwind Auction House. Char: Goldhand. EU-Alliance.

The bot does not enforce this format — it's a community convention. Posting it makes the user's job (find you in-game) easy.

---

## 6. The trade

In-game, the trade direction depends on the ticket type:

- **Deposit ticket**: the user gives you gold. You count what they put in the trade window. It must match the ticket amount exactly. If they mistype, ask them to retry; do not "round up" by trading some back.
- **Withdraw ticket**: you give the user gold. The amount to give them is `amount_delivered` from the embed (which equals `amount` — the fee was already captured at create-time). Do NOT include the fee in your trade.

Confirm the trade window contents exactly match. Then click Trade.

---

## 7. `/confirm` (the 2FA modal)

After the in-game trade is complete, run `/confirm` inside the private ticket channel. A 2FA modal pops up:

```
[ Type CONFIRM to commit ]   ← single-input modal for cashier confirms
```

Type `CONFIRM` (case-sensitive) and Submit. The bot:

- Calls the right SDF (`dw.confirm_deposit` or `dw.confirm_withdraw`) inside a single transaction.
- Updates the user's balance (deposit only — withdraw was debited at create-time).
- Writes the audit row.
- Edits the ticket embed to "Confirmed".

> **You cannot un-confirm.** Once the SDF commits, the audit row is immutable. If you confirmed in error, see `disputes.md` and `treasury-management.md` — admins can refund via treasury operations, but only post-hoc.

---

## 8. Releasing or cancelling

If something goes wrong before confirm, you have two outs:

```
/release       — give up your claim; the ticket goes back to 'open'; another cashier can claim
/cancel        — cancel the ticket entirely; refunds the user (withdraw) or just closes (deposit)
```

A common workflow: you claim, post your meeting location, the user goes AFK for an hour. The `claim_idle` worker (5 min cadence) auto-releases your claim if no message has been posted in the channel for 60 min — you don't have to remember to release.

> **Only YOU (the claimer) can release your own claim.** A different cashier or an admin must use `/admin-force-release` if they want to take it from you. The SDF guards this with a `wrong_cashier` exception.

---

## 9. Going on break

Need to step away for 10 minutes? Don't fully sign off:

```
/cashier-break
```

This sets `state='break'`. While on break:

- You're still listed in `#online-cashiers` but in the "On break" bucket.
- The cashier-alert routing skips you for new tickets.
- Tickets you've already claimed are unaffected; you can still `/confirm` them.

Run `/cashier-online` again to come back from break.

---

## 10. Going offline

End of shift:

```
/cashier-offline
```

This sets `state='offline'`. You disappear from `#online-cashiers`. Any tickets you'd claimed and not confirmed get auto-released by the `claim_idle` worker after 5 min.

---

## 11. Your stats

```
/cashier-mystats
```

Shows your shift summary:

- Total tickets confirmed (deposit + withdraw, last 24h / last 7d / all-time).
- Total gold-volume confirmed.
- Average claim → confirm latency.
- Dispute involvement count (tickets you confirmed that later got disputed).
- Last activity timestamp.

The aggregate is recomputed every 15 min by the `stats_aggregator` worker.

> **Admin equivalent**: `/admin-cashier-stats cashier:@<username>` shows the same view for any cashier.

---

## 12. Cashier etiquette

- **Always announce in-game contact** — first message after `/claim` should be your character + location.
- **Don't claim what you can't fulfil** — if you're EU-Alliance only, don't claim NA-Horde even if the bot would let you (it won't, the SDF guards this).
- **If you're not sure about the trade amount, ask** — the embed has it in plain text. Re-read before trading.
- **`CONFIRM` only after the gold has changed hands.** Do not pre-confirm "we trust each other" — the audit log is the trust.
- **Disputes are normal.** If a user disputes a ticket you confirmed, don't take it personally; admins will review the audit log.

---

## 13. Worked example: a full shift

20:00 UTC, Friday. You're a EU-Alliance cashier registered as `Goldfinger`.

1. **20:00** — Run `/cashier-online` in `#cashier-onboarding`. You appear in `#online-cashiers` under "EU-Alliance".
2. **20:04** — Alert in `#cashier-alerts`: `GRD-3F2A — deposit, EU, Alliance, 50 000 G, user @Aelara`. You go to `#deposit-grd-3f2a`, run `/claim`.
3. **20:04** — You post: `"Meet at SW AH. Char: Goldfinger."`.
4. **20:07** — Aelara trades you 50 000 G. You hit Trade.
5. **20:08** — You run `/confirm` in the channel, type `CONFIRM`. Aelara's balance is now 50 000.
6. **20:14** — Another alert: `GRD-9B1D — withdraw, EU, Alliance, 200 000 G, user @Drogosh`. You claim, post meet info.
7. **20:18** — You trade Drogosh 200 000 G. `/confirm`. Done.
8. **22:30** — End of shift. Run `/cashier-offline`.

Run `/cashier-mystats` to see today's two confirms credited.

---

## 14. References

- `deposit-flow.md` and `withdraw-flow.md` — the user side of each flow
- `ticket-lifecycle.md` — formal state machine
- `disputes.md` — when a confirmed ticket is later challenged
- ADR 0014 (cashier online status model)
- ADR 0016 (2FA modals for money operations)
- D/W design spec §5.7 (cashier system)
