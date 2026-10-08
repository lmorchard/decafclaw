# Notes: Complete Approval Timeout Resolution (#969)

## Analysis
- PR #976 made confirmation timeouts disabled by default (`confirmation_timeout_sec: None`), but missed that `execute_tool`'s `_run_with_cancel` wrapped tools like `shell` with `tool_timeout_sec: 180`.
- Shell command execution once approved is already bounded by 30s subprocess timeout in `_execute_command`. Unapproved shell commands waiting on human confirmation were being terminated by the 180s watchdog.
- Mattermost emoji reaction polling had a hardcoded default of 60s and ignored the confirmation event's timeout parameter.

## Implementation Details
1. **Context & Confirmation Coordination**:
   - Added `confirmation_active: asyncio.Event` to `Context` and fork routines.
   - `request_confirmation` activates `ctx.confirmation_active` while waiting on user response.
   - `_run_with_cancel` in `tools/__init__.py` detects `confirmation_active.is_set()` when the execution timer fires and pauses cancellation until the user confirms or cancels.
2. **Tool Definitions Opt-Out**:
   - Added explicit `timeout: None` declarations across confirmation-gated tools: `shell`, `shell_guidance`, `shell_background_start`, `activate_skill`, `admin_*`, `http_request`, `send_email`, and `vault_*` mutation tools.
3. **Mattermost Polling**:
   - `on_confirm_request` passes `event.get("timeout")` into `_poll_confirmation_manager`.
   - `_poll_confirmation_manager` supports `timeout=None` for indefinite emoji reaction polling and exits early if `manager.get_state()` shows confirmation is no longer pending.
4. **Verification**:
   - `tests/test_tool_timeout.py`: 13 passed including confirmation pause test and timeout: None opt-out regression assertions.
   - `tests/test_http_server.py`: 26 passed including Mattermost indefinite polling and state clearing tests.
   - `ruff format` and `ruff check` passed.
