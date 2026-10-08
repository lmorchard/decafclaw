# Plan: Complete Approval Timeout Resolution (#969)

## 1. Context & Coordination
- Add `confirmation_active: asyncio.Event` to `Context` (`src/decafclaw/context.py`).
- In `request_confirmation` (`src/decafclaw/tools/confirmation.py`), set `confirmation_active` before waiting on manager/event bus, and clear in `finally`.
- In `_run_with_cancel` (`src/decafclaw/tools/__init__.py`), pass `ctx=ctx` and detect if `ctx.confirmation_active` is set when timer task completes. If active, wait for confirmation resolution before resuming or resetting the execution timer.

## 2. Tool Definitions Opt-Out
- Declare `"timeout": None` on:
  - `src/decafclaw/tools/shell_tools.py`: `shell`, `shell_guidance`
  - `src/decafclaw/skills/background/tools.py`: `shell_background_start`
  - `src/decafclaw/tools/skill_tools.py`: `activate_skill`
  - `src/decafclaw/tools/admin_tools.py`: `admin_write`, `admin_replace_lines`, `admin_edit`, `admin_delete`
  - `src/decafclaw/tools/http_tools.py`: `http_request`
  - `src/decafclaw/tools/email_tools.py`: `send_email`
  - `src/decafclaw/skills/vault/tools.py`: `vault_write`, `vault_delete`, `vault_rename`, `vault_grant_folder`, `vault_update_frontmatter`

## 3. Mattermost Polling
- In `src/decafclaw/mattermost.py`:
  - Pass `timeout=event.get("timeout")` in `on_confirm_request`.
  - Update `_poll_confirmation_manager` signature to `timeout: float | None = None`.
  - Support indefinite polling (`deadline is None`) with early return if `manager.get_state(conv_id)` indicates the confirmation is no longer pending.

## 4. Tests & Documentation
- Update `CLAUDE.md` to document the expanded opt-out set and interactive pause behavior.
- Add tests in `tests/test_tool_timeout.py` for confirmation active pause and confirmation tool definition opt-outs.
- Add tests in `tests/test_http_server.py` for Mattermost indefinite polling and state clearing.
- Run full test suite and lint checks.
