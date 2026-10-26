# feat-1148-mcp-startup-readiness

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1148
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1148-mcp-startup-readiness
- Branch: feat-1148-mcp-startup-readiness
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Next action: Done; PR updated with Copilot review fixes.

## Log

- 2026-10-26: Initialized worktree and verified baseline `make check` and `make test` (4833 passed).
- 2026-10-26: Drafted spec.md and plan.md for MCP startup and readiness lifecycle.
- 2026-10-26: Implemented background concurrent MCP connections in `mcp_client.py` with `connecting`, `connected`, `failed`, `disconnected` states and events (`mcp_server_connecting`, `mcp_server_connected`, `mcp_server_failed`, `mcp_server_disconnected`).
- 2026-10-26: Added `wait_for_server` and `wait_for_servers` readiness methods and error types (`MCPReadinessError`, `MCPTimeoutError`, `MCPConnectionError`).
- 2026-10-26: Implemented task tracking and cancellation in `disconnect_all` and reload (`_restart`) to prevent orphaned processes or tasks.
- 2026-10-26: Added safe turn boundary tool freezing and reconciliation in `agent.py`, `context.py`, and `tool_definitions.py`.
- 2026-10-26: Added `required_servers` and `server_timeout` to `ScheduleTask` with validation and wait in `schedules.py`.
- 2026-10-26: Added comprehensive deterministic lifecycle tests in `tests/test_mcp_client.py` and runner tests in `tests/test_runner.py`.
- 2026-10-26: Verified `make fmt`, `make check`, and `make test` (4851 passed, 2 skipped).
- 2026-10-26: Published draft PR #1179.
- 2026-10-26: Addressed review findings (Finding 1-3, Notes 1, 3). Re-verified (4854 passed, 2 skipped).
- 2026-10-26: Addressed Copilot review findings:
  - Added connection generation counter `_generation: int` on `MCPServerState` so replacement connections drain previous attempts without clobbering replacement's `_exit_stack` or status.
  - Cancelled, drained, and started tracked replacement connection on single server restart in `_restart`.
  - Added `required_servers` and `server_timeout` to `ScheduleUpdateRequest` in `http_server.py` and regenerated API client.
  - Added tests for connection replacement draining, single server restart replacement, and PUT `/api/schedules/{name}` with readiness fields.
  - Re-verified `make fmt`, `make check`, and `make test` (4857 passed, 2 skipped).
