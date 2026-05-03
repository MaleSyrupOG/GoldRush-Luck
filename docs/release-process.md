# Release process

> **Status**: stub (Story 1.5). Full content lands in Story 14.x (Luck launch — equivalent to D/W Story 15.4).

## Versioning

Per-bot SemVer prefixed with the bot name:

- `dw-v1.0.0`, `dw-v1.0.1`, `dw-v1.1.0`, …
- `luck-v1.0.0`, `luck-v1.0.1`, …
- `poker-v1.0.0`, … (future)

## Release checklist

TODO: Story 14.x — per-bot launch checklist. Reference template:

1. Final security review (`docs/security-review-<bot>-YYYY-MM-DD.md`).
2. Smoke test against the staging guild (`tests/reports/<bot>-smoke-YYYY-MM-DD.md`).
3. Stress test report (`tests/reports/<bot>-stress-YYYY-MM-DD.md`).
4. Changelog entry written (`docs/changelog.md`).
5. Tag the release commit: `git tag -a <bot>-v1.0.0 -m "..."`.
6. Push the tag: `git push origin <bot>-v1.0.0`.
7. Deploy to production VPS (per `runbook.md` §4).
8. 48-hour watch window with active monitoring.

## Hotfix process

TODO: Story 14.x — for an emergency fix:
1. Branch from the release tag.
2. Apply the minimal fix.
3. Tag as `<bot>-v1.0.1` (patch increment).
4. Deploy.
5. Forward-port to `main` via standard PR.

## Reference: the dw-v1.0.0 release

The Deposit/Withdraw bot's first production release shipped on 2026-05-03. The full record lives in `docs/sessions/2026-04-29_to_2026-05-03-session-log.md` §17 (Story 15.x), `docs/changelog.md` (the dw-v1.0.0 entry), and the tag `dw-v1.0.0` (commit `5256c84`).

## References

- `changelog.md`
- `runbook.md` §4 (deploy procedure)
- `docs/security-review-dw-2026-05-03.md` (template)
