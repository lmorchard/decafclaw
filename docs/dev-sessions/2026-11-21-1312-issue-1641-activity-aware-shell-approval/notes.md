# issue-1641-activity-aware-shell-approval

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1641
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1641-activity-aware-shell-approval
- Branch: issue-1641-activity-aware-shell-approval
- Base: origin/main at 1da41bd9144eca953c2523d15469fc687f9cfe89
- State: implementation complete locally; full `make check` and `make test` run before commit.
- Scope: Phases 0–3 of the design (instrumentation, deterministic read-only tier, judge risk semantics, activity direction). Phases 4 (global scope rulebook / promotion ledger) and 5 (notification polarity) remain on the issue.

## Decisions

- **Activity is recomputed from `ctx.history`, never persisted.** A sliding window (`shell.activity_window`, default 50 shell calls) makes compaction and restart reset direction for free; no new lifecycle hooks or admin state.
- **cwd names the primary domain; command tokens add domains.** A command reaching into another project counts as drift.
- **`unknown` is a real activity.** An unattributed first command asks once, then later unattributed commands are in-direction and reach the judge.
- **New activity never reaches the judge.** It prompts once with the direction stated (and is denied with the reason on unattended turns).
- **The judge may decline only for a named risk category.** No category means allow; native-tool feedback is advisory and is appended to the tool result.
- **Read-only tier runs after the security monitor and blanket pre-approval,** so Tier 2/ASK behavior is unchanged.
- **Telemetry is fail-open**; a missing bus or test double cannot change an approval decision.

## Evidence

- New modules: `src/decafclaw/activity.py`, `src/decafclaw/shell_readonly.py`, `src/decafclaw/approval_report.py`.
- New tests: `tests/test_activity.py`, `tests/test_shell_readonly.py`, `tests/test_shell_approval_activity.py`, `tests/test_approval_report.py`.
- Existing judge tests moved to mutating commands and seeded activity where they exercise the judge tier; read-only commands now intentionally bypass it.
- `make check`: pass (lint, typecheck, fmt, generated types, browser assets).
- `make test`: 6269 passed, 2 skipped at baseline 6149 before the change. Two timing-sensitive tests flaked only under parallel load and passed in isolation (`test_pin_sticky_emit_timeout_does_not_block_reader`, `test_above_budget_returns_job_handle_same_process`).

## Not done

- Phase 4: global, scope-keyed rulebook + decisions ledger + promotion/demotion + automatic condensation retarget.
- Phase 5: notification polarity (quiet auto-approvals, ntfy/modal only for risk/denials).
- Golden-corpus replay and live shadow mode (§8.3–8.4) were not run: the audit log only has approval events from this change forward, and no live deployment was touched.
