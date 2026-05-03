# Disputes (admin guide + user FAQ)

When a confirmed ticket is later challenged, the dispute mechanism is how the bot tracks the investigation and the resolution. This guide covers both sides: how a user opens or responds to a dispute, and how an admin investigates and resolves it.

---

## 1. When to use the dispute mechanism

A dispute is for **post-confirm problems**: situations where the audit log says "ticket confirmed" but reality disagrees. Examples:

- The cashier confirmed but never traded the gold in-game (or traded a different amount).
- The user claims they didn't trade the amount in the ticket but the cashier confirmed anyway.
- A misclick or coercion led to a confirm that should not have happened.

**For pre-confirm problems**, just cancel: `/cancel-mine` (user), `/cancel` (cashier), `/admin-force-cancel-ticket` (admin). The dispute mechanism is for things the cancel path can no longer fix.

If the bot says you're banned but you don't think you should be, that's also a dispute path — see §6 below.

---

## 2. How a user opens a dispute

You don't have a slash command to open a dispute. The dispute opener is admin-only via `/admin-dispute-open ticket:GRD-XXXX reason:<text>`. As a user, you raise the concern in the ticket's private channel (which still exists post-confirm) and ping `@admin`. The admin reads the channel, then opens the dispute on your behalf.

Why admin-only? To prevent dispute spam from blocking other workflows. Every `/admin-dispute-open` requires admin judgement that the concern is legitimate. Repeat-offender users who file fake disputes get blacklisted (see §5).

---

## 3. How an admin opens a dispute

```
/admin-dispute-open ticket:GRD-A1B2 reason:"User claims cashier short-traded by 50k; investigating."
```

This calls `dw.open_dispute(ticket_uid, opener_id, reason)`:

1. Validates the ticket exists and is in state `confirmed` (you can't dispute an `open`/`claimed`/`cancelled` ticket — those are pre-confirm and cancel-able).
2. Inserts a row in `dw.disputes` with `state='open'`, `opener=admin discord_id`, `reason`.
3. Writes a `dispute_opened` audit row.
4. Posts an embed in `#disputes` with the ticket details, opener, reason, and a status of "Open — under investigation". The `dw.disputes.discord_message_id` column captures the message id so subsequent state changes EDIT this single embed in place rather than posting new ones.

The user who originally owned the ticket gets a Discord notification (a polite ping from the bot in their original ticket channel — the channel still exists, just archived).

---

## 4. The investigation

The admin reads:

- The ticket's audit-log trail (`/admin-view-audit user:@<u>` filters by user but the trail also surfaces the ticket UID).
- The original ticket channel's chat history (still visible to admins; the channel is archived but not deleted).
- Both sides of the story (user explanation; cashier explanation).

There's no formal investigation timer. v1.0.0 does not auto-escalate. Admins use their judgement.

---

## 5. Resolving a dispute

```
/admin-dispute-resolve ticket:GRD-A1B2 action:<action> notes:<text>
```

The `action` parameter is one of:

| Action | What it does |
|---|---|
| `refund_full` | Treasury withdraws to the user the full ticket amount; original ticket stays `confirmed` (audit-log integrity); the dispute records the refund |
| `refund_partial` | Same but partial — the admin specifies the amount in `notes` (free text); the bot calls treasury withdraw-to-user with that amount |
| `cashier_warning` | No money moves; cashier gets a flag in their `cashier_stats.dispute_count` |
| `user_warning` | Same but for the user; flags the user account |
| `ban_user` | Blacklists the user via `dw.ban_user`. They cannot create new tickets. |
| `ban_cashier` | Suggested workflow only — bot does NOT have `Manage Roles`, so admin must remove the `@cashier` role manually via Server Settings. The dispute action records the intent. |

Each action calls `dw.resolve_dispute(ticket_uid, action, resolver_id, notes)`:

1. Locks the dispute row `FOR UPDATE`.
2. Validates `state='open'` (can't re-resolve a closed dispute).
3. If a refund action, calls the appropriate treasury fn (which writes its own audit rows).
4. Inserts a `dispute_resolved_<action>` audit row.
5. Edits the `#disputes` embed in place: status updates to "Resolved — `<action>`" with the resolver and notes.
6. Posts a transient note to `#audit-log` so admins have two views: the long-lived dispute card in `#disputes` + the timeline in `#audit-log`.

For `refund_full` / `refund_partial`, the underlying treasury operation is `dw.treasury_withdraw_to_user` (see `treasury-management.md` §5). It writes the standard treasury audit rows, just with a `correlation_id` linking to the dispute.

---

## 6. Rejecting a dispute

If the admin investigates and concludes the dispute is unfounded:

```
/admin-dispute-reject ticket:GRD-A1B2 notes:"Cashier's audit-log evidence shows full amount was traded. User retracted complaint. Closing."
```

`dw.reject_dispute` validates `state='open'`, transitions the dispute to `state='rejected'`, writes a `dispute_rejected` audit row, edits the embed in place to "Rejected".

Repeat fake-dispute openers get banned via `/admin-ban-user`.

---

## 7. The blacklist (banned users)

The bot has a per-user blacklist. A banned user cannot create new tickets:

```
/admin-ban-user user:@<u> reason:<text>
```

This calls `dw.ban_user(target_id, actor_id, reason)`:

1. Upserts a row in `dw.blacklist` with `state='banned'`.
2. Writes a `user_banned` audit row.
3. Posts an embed in `#audit-log`.

Effect: every subsequent `dw.create_deposit_ticket` and `dw.create_withdraw_ticket` call by the banned user raises a typed exception that the cog renders as an ephemeral "you are banned" embed. The bot does NOT auto-DM the user — banning is a forensic act, not a public shaming.

To unban:

```
/admin-unban-user user:@<u> reason:<text>
```

Calls `dw.unban_user`. Idempotent (re-banning a banned user is a no-op; re-unbanning is too).

`/admin-view-audit user:@<u>` shows the ban/unban history for any user.

---

## 8. The Alertmanager rule for dispute volume

The Story 11.x alert `DeathRollHighDisputeVolume` fires if:

```
rate(deathroll_dispute_opened_total[1h]) > 5
```

I.e., more than 5 disputes opened in the last hour. The webhook posts to `#alerts`; the operator should investigate (often a single bad-actor cashier or a weekend outlier).

The metric `deathroll_cashier_dispute_rate{cashier_id}` is per-cashier dispute rate, surfaced in `/admin-cashier-stats`. A rate > 0.05 (5 % of confirms disputed) is a strong signal something is wrong with that cashier.

---

## 9. Worked example: full dispute lifecycle

**Tuesday 14:00** — User `Aelara` raised the concern in `#deposit-grd-3f2a` (an archived channel from yesterday) that the cashier `Bandit` confirmed a 50 000 deposit but only 49 000 was traded in-game. She pings `@admin`.

**Tuesday 14:05** — Admin `Aleix` reads the channel:

- Aelara: "I traded Bandit 49k but the bot says I deposited 50k. Either I miscounted or Bandit confirmed wrong."
- Bandit: "I logged the 50k from my chat-log screenshot."

Aleix runs `/admin-dispute-open ticket:GRD-3F2A reason:"User claims 1 000 short on deposit; cashier disagrees. Investigating."`. `#disputes` channel gets a card.

**Tuesday 14:10** — Aleix asks Bandit for the screenshot. Bandit DMs him a Discord screenshot of the trade window showing 50 000.

**Tuesday 14:15** — Aleix asks Aelara for her trade log. She admits the 49k was a memory mistake; the screenshot agrees with the bot.

**Tuesday 14:17** — Aleix runs `/admin-dispute-reject ticket:GRD-3F2A notes:"Trade window screenshot from cashier confirms 50 000. User retracted. No action."`.

**Tuesday 14:18** — `#disputes` card edits in place to "Rejected — no action". Audit log gets `dispute_opened` (row N), `dispute_rejected` (row N+1).

Total elapsed: 17 min. No money moved.

---

## 10. Worked example: legitimate refund

**Sunday 19:00** — User `Drogosh` reports: "I confirmed the cashier `Bandit`'s `/confirm` but Bandit never actually traded me. I have screenshots showing the trade window was empty."

Aleix opens the dispute, investigates:

- Aelara's audit-log: confirmed withdraw of 200 000 G yesterday.
- Bandit's history: 30 confirms last week, no prior disputes; one confirm today disputed.
- Drogosh's character: bag is empty (he linked his armory; the gold isn't there).
- Bandit's character: 200 000 G appeared in his bag yesterday and is still there.

Conclusion: Bandit confirmed without trading. Action: `refund_full` and `ban_cashier` (manual role removal).

**Sunday 19:30** — Aleix runs `/admin-dispute-resolve ticket:GRD-9B1D action:refund_full notes:"Cashier confirmed without trade; bag screenshot from victim + cashier's bag both verify gold not delivered. Refunding 200 000 from treasury. Cashier role to be removed manually."`. Then `/admin-treasury-withdraw-to-user user:@Drogosh amount:200000 reason:"Dispute GRD-9B1D refund_full — cashier non-delivery."`. Then he removes Bandit's `@cashier` role in Server Settings.

Audit-log rows produced: `dispute_opened`, `dispute_resolved_refund_full`, `treasury_withdraw_to_user`, `transfer_in` (Drogosh side). All linked by `correlation_id`. `#disputes` card shows "Resolved — refund_full" with the notes.

---

## 11. References

- `treasury-management.md` — the refund mechanism via treasury withdraw-to-user
- `cashier-onboarding.md` §12 — cashier conduct rules
- ADR 0011 (D/W as economic frontier — why dispute resolution routes through treasury)
- ADR 0016 (2FA modals — including the `BAN` magic word)
- D/W design spec §6.4 (anti-fraud table — the dispute mechanism is the answer to several of those vectors)
- Migration `0010_dw_disputes.py` (the dispute SDFs)
- Migration `0012_dw_blacklist.py` (the ban_user / unban_user SDFs)
