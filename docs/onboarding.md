# Contributor onboarding

> **Status**: stub (Story 1.5). Full content lands in Story 13.x.

## 30-minute ramp-up

TODO: Story 13.x — a structured "what to read in what order" path for a new contributor. Initial sketch:

1. **README.md** at repo root — project overview (5 min).
2. **`docs/architecture.md`** — high-level diagram (5 min).
3. **`docs/superpowers/specs/2026-04-29-deathroll-<bot>-v1-design.md`** — spec for whichever bot you're touching (10 min, skim).
4. **`docs/superpowers/specs/2026-04-29-deathroll-<bot>-v1-implementation-plan.md`** — Current state header (3 min, identify what's open).
5. **`docs/security.md`** + **`docs/provably-fair.md`** if the work touches money flow (5 min).
6. **`adr/0001-monorepo-layout.md`** + relevant per-bot ADRs (2 min).

## Local development setup

TODO: Story 13.x — link to `operations.md` §1 and the `Makefile` targets.

## How to make a PR

TODO: Story 13.x:
- Branch from `main`.
- One story per PR (atomic; mirrors the implementation plan's story granularity).
- Commit message: no AI / Anthropic / Claude attribution; Aleix as author.
- CI gates: ruff, mypy strict, pip-audit, tests with coverage gates.
- Update the relevant plan tracker's "Decision log" if you deviated from spec.
- Update `docs/` in the same PR (no doc-drift).

## Authorship rule

DeathRoll has a hard rule: every artefact (commits, code comments, docs, embeds, PDFs) carries Aleix as the sole author. No `Co-Authored-By` footers, no `🤖 Generated with...` markers, no AI attribution anywhere. This is non-negotiable and enforced in every contribution.

## References

- `operations.md` (deploy + dev environment setup)
- `release-process.md` (when to tag)
- `Makefile` (local commands)
