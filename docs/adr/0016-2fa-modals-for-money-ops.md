# ADR 0016 — Money operations require a 2FA modal with re-typed inputs

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

D/W operations that move gold need protection against four failure modes:

1. **Slip-of-the-finger** — a cashier hits `/confirm` on the wrong ticket; an admin types `2000000` when they meant `200000` for a treasury withdrawal; a user pastes their friend's Discord id.
2. **Click-jacking style** — a Discord button persists in a channel for hours and a passing user clicks it without realising what it does.
3. **Stolen-token attack** — a malicious actor with a valid bot token (or a phished cashier session) tries to drain the treasury automatically.
4. **No-takebacks** — once the SECURITY DEFINER fn commits, the gold has moved and the audit log is permanent.

The bot is an unsupervised, internet-facing surface. We needed a defensive UX pattern that:

- Adds a deliberate human act between "I clicked a thing" and "money moved".
- Resists script-kiddie automation (typing varies; clicking is uniform).
- Surfaces the operation's parameters one more time so the user re-reads them before committing.
- Costs the user only ~5 seconds of typing.

## Decision

**Every money-moving slash command opens a modal that re-displays the operation parameters and asks the user to re-type a 6-character magic word (and, where applicable, the amount and the recipient).** The bot only commits if the typed value matches case-sensitively.

Concrete patterns:

| Operation | Modal inputs | Notes |
|---|---|---|
| `/confirm` | "Type CONFIRM to commit" | Cashier-side; one input |
| `/admin-treasury-sweep` | "Type CONFIRM" + "Re-type the amount" | Two-input variant (`TwoInputConfirmModal`) |
| `/admin-treasury-withdraw-to-user` | "Type CONFIRM" + "Re-type the amount" + "Re-type the recipient discord id" | Three-input variant (`ThreeInputConfirmModal`) |
| `/admin-force-cancel-ticket` | "Type FORCE-CANCEL" | Distinct magic word so a habituated `CONFIRM` doesn't auto-fire |
| `/admin-ban-user` | "Type BAN to commit" | Distinct magic word; case-sensitive |

The modals live at `deathroll_deposit_withdraw/views/confirm_modals.py`. Each is a thin `discord.ui.Modal` with one to three `TextInput` rows. On submit:

1. Compare each input to the expected value (case-sensitive `==` for the magic word, integer-equal for the amount, integer-equal for the recipient id).
2. If any mismatch, abort with an ephemeral error embed naming the mismatched field. The SDF is NOT called.
3. If all match, dispatch to the SDF.

## Consequences

Positive:

- A misclick is harmless: the modal pops, the user closes it without typing, nothing happens. No money has moved.
- A script-kiddie cannot drive the modal without simulating both the click AND the typed input AND closing the modal — three Discord interactions versus the one a button-click attack would need. The marginal effort is significant.
- The user re-reads the operation's parameters a second time. When they re-type the amount they catch their own off-by-three-zeroes mistake. Documented case: a beta tester typed `1000000` (1M) intending `100000` (100k); the re-type surfaced the discrepancy.
- The audit log records the full modal-input set (post-validation) so post-hoc forensics see "yes, the admin really did intend `200000`".

Negative:

- ~5 seconds of typing per money operation. For high-volume cashiers confirming dozens of tickets per shift, this adds up. Acceptable: cashiers report it feels safer than a button-only flow.
- Discord modals have a 3-second response time after `Submit` clicks. The dispatch must complete inside that window or the bot must `defer` first. The SDFs are well under 100 ms; tested.
- The 2FA pattern adds boilerplate. Mitigated by the shared `BaseConfirmModal` parent class that handles the input-collection and validation pattern uniformly.

## Alternatives considered

- **Click-only confirm button**: rejected for the click-jacking and stolen-token reasons above.
- **Single-input always-`CONFIRM` modal**: rejected because typing the same six characters in muscle-memory becomes button-like over time. Per-operation distinct magic words (`CONFIRM`, `FORCE-CANCEL`, `BAN`) keep the typing intentional.
- **Server-side rate limiter only (no modal)**: rejected — rate limiting helps with brute-force but not with slip-of-the-finger or stolen-token. Modals are orthogonal to rate limiting; we run both.
- **OTP via authenticator app**: rejected for v1 — adds account linking and key custody surface that exceeds the threat model. Considered for v1.x if multi-admin signing is added.

## References

- D/W design spec §6.2 (treasury operations are super-restricted)
- `deathroll_deposit_withdraw/views/confirm_modals.py` (the modal classes)
- Unit tests `tests/unit/dw/test_confirm_modals.py` (mismatch paths)
- ADR 0011 (the economic-frontier discipline these modals enforce)
