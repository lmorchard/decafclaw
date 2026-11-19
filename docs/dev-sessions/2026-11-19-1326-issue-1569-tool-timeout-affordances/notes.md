# issue-1569-tool-timeout-affordances

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1569
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1569-tool-timeout-affordances
- Branch: issue-1569-tool-timeout-affordances
- Base: origin/main at 1bb40282405b6a793aa3bc5aae05af2718a0310c

## Decisions

- Error classification stays on the existing `[error` text convention, applied
  once in `tool_execution._execute_single_tool_impl`; `ToolResult` only gains
  explicit `timed_out`/`timeout_sec` fields (the watchdog is the only producer
  that cannot be derived from text).
- `Context.publish("tool_status")` stamps `ToolState.last_status_at` so the
  generic heartbeat defers to a tool's native progress. Per-call ToolState
  forks keep the stamp scoped to one tool call.
- Heartbeat interval is a module constant (15s), not new config; the issue
  named 15s and no operator knob was requested.
- Outcome tags ride the archived `role: tool` message (`is_error`,
  `timed_out`, `timeout_sec`) so reloaded history keeps the loud affordance.
- Mattermost failure text takes precedence over `display_text`; the shell
  command block is skipped on failure (the warning carries the outcome).

## Log

- 2026-11-19: Implemented watchdog warning log, `tool_end`/archive outcome
  tags, WebSocket projection + message_types manifest, web UI badge/auto-expand
  styling, Mattermost warning message, and 15s progress heartbeat with native
  status suppression.
- Targeted tests: `uv run pytest tests/test_tool_timeout.py
  tests/test_web_websocket_widgets.py tests/test_mattermost_display.py -n 0`
  → 56 passed. `make test-js` → 821 passed.
- `make check` fails check-message-types on a dirty tree by design (it
  regenerates then `git diff --exit-code`); rerun after committing.

## Live verification (2026-11-19)

- Ran the real web UI against `decafclaw.testing.ephemeral_server` with the
  scripted provider, twice.
- Timeout scenario (`TOOL_TIMEOUT_SEC=3`, scripted `wait(seconds=10)`): server
  logged `[watchdog] Tool 'wait' timed out after 3s`; the chat row rendered
  `⚠️ wait [Timed Out (3s)]` with the amber border and the error text
  auto-expanded. Reload kept the badge (archive tags) after fixing
  `message-store.#mergeToolMessages`, which had rebuilt the tool row without
  copying `is_error`/`timed_out`/`timeout_sec`.
- Heartbeat scenario (`wait(seconds=18)`): the in-flight row showed
  `wait: still running (15s)`; the completed call rendered as a normal
  success row with no badge.
- Cleaned up both servers and temp data homes; tabs closed.
