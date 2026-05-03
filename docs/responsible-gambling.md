# Responsible Gambling

> **Status**: stub (Story 1.5). Decision recorded; full content lands in Story 13.x.

## Decision (recorded)

DeathRoll Luck v1 ships **without** responsible-gambling features. Specifically NOT included:

- No deposit limits per user.
- No cool-off / self-exclusion mechanism.
- No automated session-time reminders.
- No "are you sure?" prompts on bet escalation patterns.
- No external link to gambling-help resources.

## Rationale

The platform mediates WoW Gold (a virtual asset), not real currency. Responsible-gambling features are designed for real-money operators where the harm vector is financial ruin. Within the WoW Gold context the harm vector is "you lose your virtual gold and don't have it for raids", which is materially different and not regulated as gambling in any jurisdiction the operator is exposed to.

## Revisit triggers

The decision is **not** "never add RG features". It is "v1 ships without; add when one of the following becomes true":

- The platform begins mediating real-money transactions (this would be a major architectural change anyway).
- The operator's jurisdiction changes the regulatory posture for virtual-asset gambling.
- The operator observes a user pattern that suggests harmful play (large repeated losses, mental-health signals from the player, etc.).

When a revisit happens, this doc gets a v2 with the new feature set.

## Operator-side levers

TODO: Story 13.x — what the operator CAN do without v2 RG features:

1. `/admin-ban-user` if a user is exhibiting harmful patterns.
2. Per-game min/max bet limits (configured via `/admin-set-game-limits`).
3. Manually engage with a user via DM if a pattern emerges.
4. Reach out to Discord's safety / wellbeing resources if the operator becomes concerned about a real-life situation.

## References

- Luck design spec §1.2 (non-goals — RG features deferred)
- D/W spec `compliance.md`
