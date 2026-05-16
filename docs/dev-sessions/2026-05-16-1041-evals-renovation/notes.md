# Evals renovation — session notes

Running log for this dev session. One section per PR; end-of-session retro at the bottom.

## Pre-flight

- Re-audit captured in `audit.md`. Baseline: 24/30 pass (80.0%) on `vertex-gemini-flash`, bundle at `evals/results/2026-05-16-1041-vertex-gemini-flash/`.
- Spec + plan in `spec.md` / `plan.md`. Four PRs in dependency order.

### Re-audit highlights

- PR #429 smoke test (`saves memory when asked`) is bit-rotted: `notes_append` competes with `vault_journal_append`. Hardest finding from the re-audit; will be fixed in PR-B via tool-description tightening.
- Other failures are mostly the same shape as the 2026-04-24 audit: vague memory prompts don't trigger retrieval; `project_update_plan` registry confusion still alive (#355 still open).
- No new harness gaps surfaced — the renovation plan covers all known harness needs.

## PR-A — Harness polish

_To fill in during execution._

## PR-B — Vault + memory cleanup + notes/vault disambiguation

_To fill in during execution._

## PR-C — Tool-selection coverage sweep

_To fill in during execution._

## PR-D — Pass-rate trend tracking

_To fill in during execution._

## End-of-session retro

_To fill in at end._
