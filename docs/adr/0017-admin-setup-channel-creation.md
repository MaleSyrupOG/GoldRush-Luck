# ADR 0017 — `/admin-setup` self-creates the guild's channel layout

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-05-04 |
| Author | Aleix |

## Context

The D/W bot needs a specific set of channels in the host guild to function:

- A `Banking` category containing `#deposit`, `#withdraw`, `#audit-log`, `#disputes`, and `#alerts`.
- A `Cashier` category containing `#cashier-onboarding`, `#cashier-alerts`, `#cashier-stats`, `#online-cashiers`.
- Two welcome embeds in `#how-to-deposit` and `#how-to-withdraw` (deferred until first `/admin-setup`).
- Per-channel permission overwrites that bind the `@cashier` and `@admin` roles to the right channels.

In a fresh guild, none of this exists. The operator could:

1. **Read a runbook and create everything manually** — copy/paste a 9-channel checklist with permissions; error-prone, slow, untested-by-the-bot.
2. **Run an external setup script** that hits the Discord API outside the bot — adds an out-of-band tool with its own auth surface.
3. **Have the bot create everything itself**, on demand, idempotently.

## Decision

**An admin runs `/admin-setup` once. The bot creates every required channel, category, and permission overwrite.** The command is idempotent: re-running it after a partial setup completes the missing pieces without duplicating anything.

Implementation lives at `deathroll_deposit_withdraw/setup/channel_factory.py::setup_or_reuse_channels(guild, *, cashier_role_id, admin_role_id, dry_run=False, persist=None)`. It:

1. Iterates the canonical layout (defined as a constant tuple-of-dicts in the same file).
2. For each category, looks up by name; if absent, creates it.
3. For each channel, looks up by `(name, parent_category_id)`; if absent, creates it with the spec §5.3 permission matrix; if present, leaves it alone (no overwrite stomping).
4. After the channel exists, writes the resolved Discord channel id into `dw.global_config` under the key `channel_id_<key>` via the injected `persist` callback.
5. Returns a `SetupReport` enumerating each entity as `created` or `reused`.
6. The `dry_run=True` path produces the same report without making any Discord API calls — useful for "show me what would change" admin previews.

The slash command `/admin-setup` (in `deathroll_deposit_withdraw/cogs/admin.py`):

1. Defers the interaction (the 3-second Discord response window cannot accommodate ~10 entity creations).
2. Calls `setup_or_reuse_channels` with the user's chosen role ids (defaulting to the configured cashier/admin role ids if omitted).
3. Calls `reconcile_welcome_embeds` to seed/edit the `#how-to-deposit` / `#how-to-withdraw` / `#cashier-onboarding` embeds inline.
4. Renders a summary embed with one row per entity showing its outcome.

## Consequences

Positive:

- A new guild becomes fully operational with one command. The operator types `/admin-setup` and the bot reports "9 channels created, 3 welcome embeds posted, 0 errors".
- The setup is declarative-and-idempotent. If a channel is accidentally deleted, re-running `/admin-setup` recreates it. If a permission overwrite drifts (e.g., an admin manually adds the `@everyone` role), the spec stays the source of truth (manual drift is detected by the next setup run, currently logged as `reused` but not reconciled — flagged as future work).
- The Discord API surface for "create channel" requires `Manage Channels` permission. This is the only permission D/W needs beyond the standard slash-command permissions; documented in spec §6.5.
- The `persist` callback decouples Discord-side creation from DB-side persistence. Tests mock `persist` to verify what would have been written; production passes the real `dw.global_config` writer.

Negative:

- The bot needs `Manage Channels` permission on the guild. This is broader than necessary in principle (it could only need it inside the `Banking` and `Cashier` categories) but Discord's permission model doesn't let us scope category-creation to a sub-category. Acceptable: the same role grants we already have for moderating the guild include this permission.
- A misconfigured `/admin-setup` (wrong role ids, e.g.) creates channels with wrong overwrites. The `dry_run=True` flag mitigates this; the operator-facing docs (`docs/operations.md`) recommend always running `dry_run` first on a new guild.
- The canonical layout is hard-coded in `channel_factory.py`. Customising it (e.g., a guild that wants a different category name) requires a code change. Acceptable for v1; multi-guild customisation is deferred.

## Alternatives considered

- **External setup script**: rejected for the out-of-band-auth reason above.
- **Inline `discord.py` calls in `on_ready`**: rejected because creating channels at every bot start is slow, noisy, and runs even when not needed.
- **Operator-driven manual setup**: rejected for the error-rate reason above; the bot can't catch a typo in the operator's hand-typed `#alerts` channel name (which would silently break the alert-routing).

## References

- D/W design spec §5.3 (channel permission matrix)
- D/W design spec §7.2 (deploy checklist — "run `/admin setup` once")
- `deathroll_deposit_withdraw/setup/channel_factory.py`
- `deathroll_deposit_withdraw/cogs/admin.py::AdminCog::admin_setup`
- Unit test `tests/unit/dw/test_channel_factory.py` (23 tests covering the idempotent paths)
