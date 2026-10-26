# Plan: MCP Startup & Readiness Lifecycle (#1148)

## Phase 1: Core MCP Client Lifecycle & Readiness
- Add error types (`MCPReadinessError`, `MCPTimeoutError`, `MCPConnectionError`) in `mcp_client.py`.
- Enhance `MCPServerState` with `error`, `settled_event`, and `is_ready`.
- Add task tracking (`_connection_tasks`), cancellation, draining, and closed guards to `MCPRegistry`.
- Implement `start_connect_server`, `start_connect_all`, `wait_for_server`, and `wait_for_servers`.
- Update `connect_server` to track `connecting` state, publish `mcp_server_connecting`, `mcp_server_connected`, and `mcp_server_failed` events, and handle `CancelledError` cleanly.
- Make `init_mcp` default to `wait=False` (background connection).
- Update `disconnect_all` to cancel and drain in-flight connection tasks.
- Update `skills/mcp/tools.py` (`_restart`) to drain old registry tasks on reload.

## Phase 2: Tool Freezing & Reconciling at Turn Boundaries
- Add `mcp_definitions` and `mcp_frozen` to `ToolState` in `context.py`.
- Update `tool_definitions.collect_all_tool_defs` and `refresh_dynamic_tools` to use the frozen snapshot during turns and reconcile at turn start.
- Update `TurnRunner` in `agent.py` to freeze tools across iterations and unfreeze on turn exit.

## Phase 3: Scheduled Task Required-Server Policy
- Add `required_servers` and `server_timeout` to `ScheduleTask` in `schedules.py`.
- Update `_KNOWN_FRONTMATTER_KEYS` and `VALID_PATCH_KEYS`.
- Update `parse_schedule_file` to parse `required-servers` and `server-timeout`.
- Update `run_schedule_task` to wait on `registry.wait_for_servers` with `task.server_timeout` and fail clearly on error.

## Phase 4: Observability & Health
- Update `tools/health.py` to count and report `connecting` servers.
- Update `audit_log.py` to record `mcp_server_failed` events.

## Phase 5: Checkpoint Commit & Draft PR
- Run targeted checks.
- Commit checkpoint as MokaGnome.
- Submit draft PR with `gh pr create --draft`.

## Phase 6: Deterministic Lifecycle Tests & Full Verification
- Create `tests/test_mcp_client.py` covering fast, slow, failed, canceled, late-completing connections, wait_for_servers, reload cancellation, and turn boundary freezing.
- Create `tests/test_runner.py` covering runner startup with delayed MCP handshake and clean shutdown.
- Add schedule readiness test in `tests/test_schedules.py` (or `test_mcp_client.py`).
- Run `make fmt`, `make check`, `make test`.
- Publish final commits to the same draft PR.
