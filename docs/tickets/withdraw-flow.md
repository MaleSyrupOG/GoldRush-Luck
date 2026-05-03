# Withdraw flow

How to take WoW Gold out of your DeathRoll balance and into your character's bag.

> **TL;DR**: in `#withdraw`, run `/withdraw`. The bot locks your balance and captures the fee. A private channel `#withdraw-grd-XXXX` opens. A cashier claims, trades you the net amount in-game, and confirms; your balance has already been debited at create-time.

---

## 1. Prerequisites

- You're a member of the DeathRoll guild on Discord.
- You have a **balance ≥ amount + fee** in DeathRoll.
- You have a WoW character on the region/faction you're withdrawing into.
- You're not banned.

The withdraw fee is **2 % of the amount** by default (configurable via `/admin-set-fee-withdraw`). The fee is captured at create-time, not at confirm-time.

---

## 2. Run `/withdraw`

In `#withdraw`, run `/withdraw`. Modal:

| Field | Type | Notes |
|---|---|---|
| Amount | integer | Plain digits, no suffixes |
| Character | text | The in-game character receiving the gold |
| Region | `EU` / `NA` | Case-sensitive |
| Faction | `Alliance` / `Horde` | Case-sensitive |

On submit, the bot inside a single SECURITY DEFINER transaction:

1. Locks your `core.balances` row `FOR UPDATE`.
2. Verifies `balance_g >= amount + fee`. If not, raises `InsufficientFunds`; you get an ephemeral error embed; nothing is committed.
3. Debits your balance by `amount + fee`. The fee goes to the treasury (`discord_id = 0`) inside the same transaction.
4. Inserts a row into `dw.withdraw_tickets` with state `open`, capturing `amount`, `fee`, `amount_delivered = amount` (i.e., what the cashier should trade you in-game).
5. Creates the private channel `#withdraw-grd-XXXX` and posts the open embed.
6. Pings `@cashier` in `#cashier-alerts`.
7. Writes a `withdraw_ticket_opened` audit row.

> **Why is balance debited at create-time, not confirm-time?** To prevent double-spending: if you opened two `/withdraw` tickets in parallel, both would race for the same balance. Locking at create-time makes the second one fail immediately rather than being silently merged. See ADR 0011.

---

## 3. The private ticket and cashier claim

Same shape as deposit (see `deposit-flow.md` §3 / §4). The difference: the ticket embed surfaces three numbers up-front:

- **Amount**: what you want to receive in-game (the value you typed into the modal).
- **Fee**: 2 % of amount, already debited from your balance.
- **Amount delivered**: equal to "Amount". This is what the cashier should trade you. It's surfaced as a distinct field so the cashier doesn't accidentally trade you `amount + fee`.

Worked example: you withdraw 100 000. The ticket shows `amount: 100 000 / fee: 2 000 / amount_delivered: 100 000`. Your balance dropped by 102 000. The cashier trades you 100 000.

---

## 4. The trade

Cashier claims, posts their in-game character + meeting location, you log in, you go to the location, the cashier trades you the `amount_delivered` value.

> **The trade direction is reversed from deposit.** In a deposit, you trade gold to the cashier. In a withdraw, the cashier trades gold to you.

---

## 5. The cashier confirms

After the in-game trade, the cashier runs `/confirm`. The 2FA modal asks for `CONFIRM`. On match, `dw.confirm_withdraw` runs:

- It does NOT debit your balance (already done at create-time).
- It DOES emit a `withdraw_confirmed` audit row.
- It DOES emit a `transfer_in` audit row crediting the treasury (this is the fee transfer; mechanically already happened at create-time but the audit row appears at confirm).
- The ticket transitions to `confirmed`.

Your balance is unchanged from immediately after the modal submit; the cashier's role is to verify the in-game trade actually happened.

---

## 6. Cancelling and refunds

If the ticket is cancelled (by you, the cashier, or an admin) BEFORE confirm, the bot refunds you. `dw.cancel_withdraw`:

1. Locks your `core.balances` row.
2. Credits back `amount + fee` (full refund — the fee is given back too).
3. Inserts a `withdraw_cancelled` audit row.
4. Inserts a corresponding refund audit row (`transfer_out` from treasury back to the user, mirroring the original fee capture).
5. Marks the ticket `cancelled`.

After this, you can re-open `/withdraw` with the same or different parameters.

> **You cannot cancel a confirmed withdraw.** Once the cashier confirms, the gold has been delivered. To raise a problem with a confirmed withdraw see `disputes.md`.

---

## 7. Worked example with realistic numbers

You have a balance of 500 000 G. You want to withdraw 200 000 G to your NA-Horde character `Drogosh`.

1. **Sunday 18:00 UTC** — `/withdraw`. Modal: `200000` / `Drogosh` / `NA` / `Horde`. Submit.
2. **Sunday 18:00 UTC** — `dw.create_withdraw_ticket` runs:
   - Lock `core.balances` row at `discord_id = your_id`.
   - Compute fee: `200_000 * 0.02 = 4_000`.
   - Verify `500_000 >= 200_000 + 4_000 = 204_000`. ✓
   - Debit your balance: `500_000 - 204_000 = 296_000`.
   - Credit treasury: `treasury += 4_000`.
   - Insert ticket: `amount=200_000, fee=4_000, amount_delivered=200_000`.
   - Audit row: `withdraw_ticket_opened`.
3. **Sunday 18:01 UTC** — Channel `#withdraw-grd-9b1d` opens. Embed shows the three numbers (`200,000 / 4,000 / 200,000`).
4. **Sunday 18:03 UTC** — Cashier `Bandit` claims. Posts: `"NA-Horde, Orgrimmar AH. Char: Goldhand."`.
5. **Sunday 18:08 UTC** — You log on Drogosh, meet Goldhand at the AH, trade window opens, Bandit puts 200 000 G in. You hit Trade.
6. **Sunday 18:09 UTC** — Bandit runs `/confirm`, types `CONFIRM`. `dw.confirm_withdraw` writes the audit rows. Ticket transitions to `confirmed`.
7. **Sunday 18:10 UTC** — Admin archives the channel. Done.

Final state: your balance is 296 000 (it has been since 18:00). Your Drogosh has 200 000 more in his bag. The treasury has 4 000 more. The audit log has rows for `withdraw_ticket_opened`, `withdraw_confirmed`, and the implicit fee `transfer_in` to treasury.

---

## 8. Edge cases

| Case | Behaviour |
|---|---|
| You cancel after you've physically logged off the cashier's character without trading | Cashier runs `/release` (drops their claim) or `/cancel` (cancels the ticket entirely, refunds you). Talk in the channel. |
| You disconnect mid-trade | Cashier should NOT confirm. Re-trade after you re-log in; cashier confirms once the gold has actually changed hands. |
| Cashier accidentally trades you `amount + fee` instead of `amount` | Trade back the fee — the bot only debited `amount + fee` and is set up to deliver `amount`. The fee goes to the treasury. Trading you the fee back means you net more than withdrawn. Talk in the channel + ping `@admin`. |
| You wanted to withdraw exactly your full balance | Withdraw `floor(balance / 1.02)` to leave room for the fee. Or run `/balance`, do the math, and withdraw the right amount. |
| You typed the wrong region/faction | Cancel the ticket (full refund) and re-open with the right one. The cashier matched on `EU-Alliance` will not be able to claim a `NA-Horde` ticket. |

---

## 9. Why the fee is captured at create-time

This is a deliberate design choice; see ADR 0011 and ADR 0012.

If the fee were captured at confirm-time, two parallel `/withdraw` tickets could each pass the "do you have enough balance?" check and both succeed, leaving your balance negative after both confirm. Capturing at create-time makes the lock atomic with the ticket creation — the second `/withdraw` either fails immediately or succeeds knowing the first one has already debited.

A cancellation reverses the capture cleanly. Audit-wise: every fee that lands in the treasury can be paired with a confirm or a cancel-refund.

---

## 10. References

- `deposit-flow.md` — the symmetric flow for putting gold in
- `ticket-lifecycle.md` — the formal state machine
- `disputes.md` — for problems after confirm
- ADR 0011 (D/W as economic frontier)
- D/W design spec §5.2 (withdraw flow)
