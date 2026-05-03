# ADR 0013 — Private CHANNELS, not threads, for ticket conversations

| Field | Value |
|---|---|
| Status | Accepted (supersedes the spec's original "private threads" decision) |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

A deposit or withdraw ticket needs a private space for the user, the cashier, and admins to talk: the trade location, the gold count, edge cases ("I sent 20k but the cashier saw 19,800"). This space must:

- Be invisible to other guild members.
- Persist after `confirm` so the user and admins can scroll back if a dispute opens.
- Be linkable from the audit log (each ticket carries a Discord id pointing at its conversation).
- Have a name that helps admins find it ("which thread/channel was that GRD-A1B2 ticket?").

The original D/W design spec §5.4 specified **private threads** under a `#deposit` / `#withdraw` parent channel. During implementation we discovered that the existing live DeathRoll guild used **private channels** for tickets — `deposit-grd-a1b2`, `withdraw-grd-c3d4` etc. Reference: `reference_actual_server_state.md`.

We had a choice: stay with the spec (private threads) or align with the existing convention (private channels).

## Decision

**Tickets are private channels, not private threads.** Each ticket is a fresh `discord.TextChannel` created under the `Banking` category with three explicit permission overwrites: the user (read+send), the `@cashier` role (read+send), the `@admin` role (read+send), and `@everyone` denied. The channel name follows the format `<type>-grd-<4-char>` where the 4-char component is the canonical UID slug (see ADR 0017 / spec §5.4).

The spec is correspondingly bumped to v1.1 in the same commit window as this ADR.

## Reasons we chose channels over threads

1. **Continuity with operator memory.** Aleix's pre-existing manual D/W process used private channels with the `deposit-grd-XXXX` naming. New cashiers and admins are already conditioned to look for "channels under Banking". Forcing a thread model would have made every operator re-learn the search pattern.

2. **Mobile UX.** Discord mobile renders threads in a stacked accordion under the parent channel. With ~10–30 tickets open at peak, the parent channel becomes visually unusable. Private channels render as a flat list under their category, which is the same as how cashiers already triage them.

3. **Permission granularity.** A private thread inherits its parent channel's permission overwrites and adds the participant list as a separate concept. A private channel's overwrites ARE the participant list. The latter maps 1:1 to "who can read this ticket" and is easier to verify (`channel.overwrites` is a single dict).

4. **Search and history.** A private thread's history is searchable only by participants and admins (with "View Private Threads"). discord.py 2.4.0 doesn't expose `view_private_threads` as a permission constant — it's folded into `manage_threads` upstream. Channels avoid the awkwardness; the admin role's per-channel "Read Message History" is enough.

5. **Audit-log linking.** `dw.*_tickets.thread_id` is a `BIGINT` Discord id. Whether that id resolves to a thread or a channel is invisible to the audit-log emit path. Switching to channels was a single rename of the schema column (deferred to spec v1.1) and a single change in the `setup_or_reuse_channels` helper that creates them.

## Consequences

Positive:

- One ticket = one channel. Cashiers triage by scrolling the `Banking` category sidebar, which is the same surface they used pre-bot.
- After `confirm`, the channel can be archived (read-only mode + everyone overwrites stripped to read-only) and stays in place forever as the dispute paper-trail. No thread auto-archive timer to manage.
- The `dw_tickets.thread_id` column name is now a misnomer; documented in the spec v1.1 bump and to be renamed to `discord_channel_id` in a future migration. The misnomer does not affect behaviour; the column is just a `BIGINT`.

Negative:

- More channels in the guild (one per ticket). At 30 open tickets the `Banking` category grows large but stays scannable. Discord caps a guild at 500 channels; at expected throughput this is not reachable for years.
- The `/admin-force-close-thread` slash command's name is now also a misnomer — it archives a private channel, not a thread. Renamed in spec v1.1 to `/admin-force-close-ticket`; backward-compat alias retained for the v1.0 channel name.

## Alternatives considered

- **Stay with private threads as the spec said**: rejected because of the mobile-UX, permission-granularity, and operator-continuity reasons above.
- **Hybrid (private channel for the conversation + optional thread for the audit-log embed)**: rejected — adds a second surface for users to confuse, no real value.

## References

- D/W design spec §5.4 (original private-threads decision; bumped to private-channels in spec v1.1)
- `reference_actual_server_state.md` (the live guild's pre-bot convention)
- `deathroll_deposit_withdraw/setup/channel_factory.py::setup_or_reuse_channels`
- `deathroll_deposit_withdraw/tickets/orchestration.py::open_deposit_ticket` / `open_withdraw_ticket`
