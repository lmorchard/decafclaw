# Spec: Complete Approval Timeout Resolution (#969)

## Context & Problem
Issue #969 sought to allow approval prompts to wait indefinitely when configured (and by default) so users multitasking during agent turns are not interrupted by premature prompt timeouts that trigger loops or retries.

While PR #976 added `confirmation_timeout_sec: int | None = None` and frontend countdown/indefinite badges, two critical timeout pathways still cause confirmation prompts (especially shell commands) to time out:

1. **Tool Execution Watchdog (`tool_timeout_sec`)**:
   `execute_tool` in `src/decafclaw/tools/__init__.py` wraps every non-MCP tool in `_run_with_cancel` with `timeout_sec = _resolve_tool_timeout(ctx, name)`, which falls back to `config.agent.tool_timeout_sec` (180s default). Tools like `shell`, `shell_background_start`, `activate_skill`, `admin_*`, etc., had no `"timeout": None` declaration, so if a user waited >180s before approving, the outer watchdog cancelled the tool with `[error: tool shell timed out after 180s]`.

2. **Uncoordinated Waiting During User Confirmation**:
   Even if a tool has a timeout for execution, time spent blocked awaiting interactive user confirmation should never consume the execution timeout budget.

3. **Mattermost Reaction Polling Hardcoded Timeout**:
   `MattermostClient._poll_confirmation_manager` in `src/decafclaw/mattermost.py` hardcoded `timeout=60` and was invoked without passing the event's timeout, causing emoji reaction polling to terminate after 60s regardless of configuration.

## Goals
1. Declare `"timeout": None` on all built-in confirmation-gated tools (`shell`, `shell_background_start`, `activate_skill`, `shell_guidance`, `admin_*`, `http_request`, `send_email`, `vault_*` mutation tools).
2. Add pause coordination in `_run_with_cancel`: when a tool is awaiting interactive confirmation (`ctx.confirmation_active`), the execution watchdog does not cancel the tool task.
3. Plumb `event.get("timeout")` into `_poll_confirmation_manager` in `mattermost.py` and support indefinite polling (`timeout=None`) that terminates when resolved, when the client stops, or when the confirmation is cleared in `ConversationManager`.
4. Provide comprehensive unit tests verifying both layers and documentation in `CLAUDE.md`.
