# Dev Session Plan: Issue 955 - Admin File Tools

## Phase 1: Tool Implementation
- [x] Create `src/decafclaw/tools/admin_tools.py`:
  - Path safety helper `_resolve_admin_path(config, path_str, allow_root=False)`:
    - Resolves target against `config.agent_path`.
    - Enforces containment within `config.agent_path`.
    - Strictly rejects targets resolving inside `config.workspace_path`.
    - Handles directory existence and file checks.
  - Diff formatting helper `_mini_diff(old_text, new_text, path)` and preview formatting.
  - Mutation gate helper `_confirm_admin_mutation(ctx, tool_name, path_str, preview)`:
    - Checks `ctx.is_unattended` and `ctx.is_child` -> returns error immediately.
    - If `ctx.request_confirmation is None` -> returns non-interactive error.
    - Calls `request_confirmation(ctx, tool_name=tool_name, command=..., message=..., approve_label="Approve", deny_label="Deny")`.
    - Returns None if approved, or `ToolResult` error if denied.
  - Implement tools:
    - `tool_admin_read`
    - `tool_admin_list`
    - `tool_admin_write`
    - `tool_admin_replace_lines`
    - `tool_admin_edit`
    - `tool_admin_delete`
  - Export `ADMIN_TOOLS` and `ADMIN_TOOL_DEFINITIONS` with `priority: "normal"`.

## Phase 2: Registry & Delegation Integration
- [x] In `src/decafclaw/tools/__init__.py`:
  - Import `ADMIN_TOOL_DEFINITIONS`, `ADMIN_TOOLS` from `.admin_tools`.
  - Add to `TOOLS` and `TOOL_DEFINITIONS`.
- [x] In `src/decafclaw/tools/delegate.py`:
  - Add `_ADMIN_WRITE_TOOLS = frozenset({"admin_write", "admin_replace_lines", "admin_edit", "admin_delete"})`.
  - Exclude `_ADMIN_WRITE_TOOLS` from `child_ctx.tools.allowed`.
- [x] In `src/decafclaw/eval/diagnostics.py`:
  - Add `"admin_read": "path"` to `READ_TOOL_ARGS`.

## Phase 3: Unit & Integration Tests
- [x] Create `tests/test_admin_tools.py`:
  - Test path containment:
    - Normal resolution under `agent_path` succeeds.
    - Directory traversal `../../etc/passwd` rejected.
    - Absolute path `/etc/passwd` rejected.
    - Path inside `workspace` rejected with redirection note.
    - Reject root deletion/overwrite.
  - Test `admin_read` and `admin_list`:
    - Reading existing file, line ranges, missing file error.
    - Listing root and subdirectories.
  - Test `admin_write`, `admin_replace_lines`, `admin_edit`, `admin_delete`:
    - Confirmation approved -> file modified, returns summary + diff.
    - Confirmation denied -> returns denial error, file unmodified.
    - Unattended turn (`ctx.task_mode = "scheduled"`) -> returns error immediately without prompting.
    - Child context (`ctx.is_child = True`) -> rejected.
  - Test `delegate_task` excludes `admin_write` tools for child agents.

## Phase 4: Quality Gates & Verification
- [x] Run targeted tests: `pytest tests/test_admin_tools.py` (29 passed).
- [x] Check formatting and types: `ruff check`, `ruff format`, `pyright` (all clean).
- [x] Update documentation (`docs/tools.md`, `CLAUDE.md`).
- [x] Document results in `notes.md`.
