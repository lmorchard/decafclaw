# Dev Session Notes: Issues 1095, 1103, 1107

- Date: 2026-10-21
- Branch: `issue-1095-1103-1107-async-scheduling-waits`
- Worktree: `.claude/worktrees/issue-1095-1103-1107-async-scheduling-waits`

## Decisions & Discoveries
- Verified PR #1102 was already merged (`make run-dev`). Clarified with user that Issue #1103 ("Runner: First-class asynchronous turn scheduling") was intended to be tackled alongside #1095 and #1107.
- Baseline `make test` passed cleanly with 4655 passed, 2 skipped.
- Baseline `make check` passed with 0 errors.
