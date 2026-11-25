# issue-1846-background-status-wait-cursor

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1846
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1846-background-status-wait-cursor
- Branch: issue-1846-background-status-wait-cursor
- Base: origin/main at 5a44da8c930a6be84c94a7d47e7161c7b459cf35
- Next action: commit, publish draft, self-review, mark ready.

## Decisions

- `wait` blocks on a per-job `asyncio.Event`, set at the top of
  `_finalize_job` so status returns before archive/notification/wake work.
- The cursor is server-owned on `BackgroundJob` (`cursor_stdout`,
  `cursor_stderr`) with absolute `stdout_total_lines`/`stderr_total_lines`
  counters that survive the 500-line deque eviction. The job manager is
  per-conversation, so this is per `(conversation, job)`.
- Cursor granularity is lines, as proposed.
- A cursor that fell behind the buffer returns the whole buffer and reports
  the gap; a finished job's next cursor read returns everything unread, then
  latches with an explicit "cursor caught up" note.
- Running-job default is the last 20 lines; a finished job always returns all
  buffered output. `tail_lines` overrides the running default. Cursor reads
  ignore `tail_lines` so the unread delta is complete.
- `from_start=true` re-reads the buffer without advancing the cursor and is
  mutually exclusive with `cursor="auto"`.
- `shell_background_status` now has `timeout: None` so `wait` can exceed the
  default tool timeout.

## Evidence

- `uv run pytest tests/test_background_tools.py -q -n 0` → 106 passed.
- `uv run pytest tests/test_background_tools.py tests/test_shell_auto_background.py tests/test_background_wake_integration.py tests/test_context_composer_assembly.py -q -n 0` → 191 passed.
- `make check` → passed.
- `make test` → 7057 passed, 3 skipped; only the four pre-existing
  `tests/test_host_metrics.py` host failures.

## Review notes

- Same-model self-review found and fixed: a caught-up cursor on a
  stopped/expired job must not print "none captured before termination"
  (`format_status_text(captured_output=...)`); empty streams should not get
  cursor notes; a completion race at the wait timeout now reports the actual
  status.
