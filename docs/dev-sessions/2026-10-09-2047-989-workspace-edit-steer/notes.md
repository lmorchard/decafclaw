# 989 — notes

## Session summary
Retarget `workspace_edit` as the default surgical-edit tool (no new tool): description rewrite, `normal`→`critical`, AGENT.md guide + git-checkout rule, registry test, 2 tool_choice cases + 1 full-loop eval, docs. All in the worktree `issue/989-workspace-edit-steer`.

## Commits (pre-rebase shas shown; rebased onto origin/main @5a6e0ca before PR)
- Phase 1: retarget workspace_edit (be99cc9)
- Phase 3: eval guards (d73c796)
- Phase 2: AGENT.md guide (104a6b0)
- Phase 4: docs (4580c20)

## Verification evidence
- Baseline (unmodified def, captured via temp file swap + restore): tool_choice `workspace-edit-*` 0/2 (model picked `workspace_read`), full-loop FAIL (wrong path).
- After: tool_choice 2/2 (5/5 reps), full-loop PASS 5 consecutive reps. No existing tool_choice case regresses — the only 5 pre-existing failures (vault-vs-workspace-read, workspace-read-vs-vault-read, canvas, 2× tabstack) are identical before/after and none touch `workspace_edit`.
- `make check` exit 0; `make test` green (4416 passed after rebase).
- Adversarial subagent review: **no serious defects.** I1–I4, T1–T4, S1/S3/S4 all verified by the reviewer.

## Findings & dispositions
- **[LOW, reviewer] description guard is phrase-narrow.** `test_no_longer_defers_multiline_to_replace_lines` (tests/test_tool_registry.py) fails only on the exact "prefer workspace_replace_lines for multi-line" wording; a reword could slip past it. Disposition: **documented limitation, intentionally accepted.** The real guard is the behavioral `tool_choice` case (`workspace-edit-*`), which the project convention (AGENTS.md Evals) says is the load-bearing disambiguation check. A multi-phrase unit matcher would be brittle and rot. Not a blocker.
- **[PREMISE] vault note text differs from the issue's quote.** The issue says the live Agent note reads "I have repeatedly misused `workspace_edit` for multi-line content". The actual live note at `data/decafclaw/workspace/vault/agent/pages/DecafClaw.md:32` is milder and positive: *"Use `workspace_edit` for exact string replacements — it's the safest editing tool because it fails if the match is ambiguous."* It does NOT steer away from `workspace_edit`; it just frames it as small/exact string replacement and omits the copy-from-fresh-read lesson. So item 3 is an **improvement, not a fix of a steer away.** The drafted new text adds the lesson and mentions multi-line.

## Pre-existing eval flakes (not regressions) — observed in both baseline and after runs
- `reads a known path directly without searching` and `vault-read-vs-workspace-read-known-page` / `workspace-read-vs-vault-read-config` flake at ~50% (model sometimes uses `admin_read`). Unrelated to `workspace_edit`. Same failure set before/after the Phase 1 change.

## Eval history (make eval-history)
Baseline 3/5 (60%) → after 4/5 (80%) — the +1 is the new `multi-line change` case going FAIL→PASS.

## Open
- **Awaiting Les:** sign-off on the vault-note new text (PR description). Not applied — live agent data, and the spec requires review first.
- **Awaiting Les:** PR approval to push + open.
