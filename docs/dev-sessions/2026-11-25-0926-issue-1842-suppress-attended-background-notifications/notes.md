# issue-1842-suppress-attended-background-notifications

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1842
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1842-suppress-attended-background-notifications
- Branch: issue-1842-suppress-attended-background-notifications
- Base: origin/main at 5a44da8c930a6be84c94a7d47e7161c7b459cf35
- Next action: review the committed diff, publish the draft PR, self-review, mark ready.

## Decision

The issue body is only a screenshot of a bell full of "Background job
completed/failed" entries. Interpreted as: jobs started in an attended (live)
conversation must not append an inbox record, because the WAKE turn already
reports the result in that conversation. Unattended jobs (heartbeat, schedule,
child work) keep their inbox notification. No new config switch: the turn's
attendance is the rule.

## Implementation

- `BackgroundJob.attended` records `not ctx.is_unattended` at start.
- `_notify_job_exit` returns early for attended jobs.
- Both start paths pass attendance: `shell_background_start` and the
  foreground-budget handoff in `shell_tools`.
- Archive record and WAKE turn are unchanged for both kinds.
- Docs updated in `background-wake.md`, `notifications.md`, `tools.md`.

## Evidence

- `uv run pytest tests/test_background_tools.py tests/test_shell_auto_background.py tests/test_background_wake_integration.py -q -n 0`
  → 116 passed.
- `uv run pytest tests/test_shell_resource_limits.py -q -n 0` → 11 passed.
- `make check` → passed.
- `make test` → 7048 passed, 5 failed: four pre-existing
  `tests/test_host_metrics.py` host failures, plus
  `test_above_budget_returns_job_handle_same_process` (ERROR vs STOPPED),
  which passes alone (`-n 0`) and is the known background-tools xdist flake.
  An earlier run also showed the known confirmation-timeout xdist flake.
