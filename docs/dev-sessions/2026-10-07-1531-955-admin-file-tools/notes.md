# Dev Session Notes: Issue 955 - Admin File Tools

## Session Overview
- **Issue**: #955 Confirmation-gated file operations outside workspace for admin skills and data files
- **Selected Approach**: Option A — Dedicated `admin_*` file tools (`admin_read`, `admin_list`, `admin_write`, `admin_replace_lines`, `admin_edit`, `admin_delete`) rooted at `config.agent_path`.
- **Branch**: `feat/955-admin-file-tools`

## Implementation Details
1. **Core Admin File Tools (`src/decafclaw/tools/admin_tools.py`)**:
   - `_resolve_admin_path`: Enforces strict containment to `config.agent_path` and rejects any path attempting directory traversal (`..`) or absolute escape.
   - Workspace boundary guard: Rejects any path resolving inside `config.workspace_path` with an explicit directive pointing to `workspace_*` tools.
   - Interactive confirmation gate (`_confirm_admin_mutation`):
     - Blocks immediately if `ctx.is_unattended` or `ctx.is_child` (no prompts on unattended turns or child agents).
     - Formats unified diff previews against existing files (or `(new file)` / delete notices).
     - Sets `approve_label="Approve"` and `deny_label="Deny"` to suppress "Always" buttons in the UI, enforcing per-mutation user review.
   - Registered tools:
     - `admin_read`: Reads text files under `config.agent_path` with line numbering and capping.
     - `admin_list`: Lists directories and files under `config.agent_path`.
     - `admin_write`: Creates or overwrites files with diff confirmation.
     - `admin_replace_lines`: Replaces line ranges with diff confirmation.
     - `admin_edit`: Exact string replacements with diff confirmation.
     - `admin_delete`: Deletes files or directories (with recursive flag) with confirmation.
2. **Registry Integration (`src/decafclaw/tools/__init__.py`)**:
   - Imported and included `ADMIN_TOOL_DEFINITIONS` and `ADMIN_TOOLS` into the agent's core tool catalog.
   - Tagged definitions with `priority: "normal"`, making them discoverable via `tool_search`.
3. **Child Agent Isolation (`src/decafclaw/tools/delegate.py`)**:
   - Added `_ADMIN_WRITE_TOOLS = frozenset({"admin_write", "admin_replace_lines", "admin_edit", "admin_delete"})`.
   - Excluded `_ADMIN_WRITE_TOOLS` from `child_ctx.tools.allowed` in `run_child_turn`.
4. **Eval Diagnostics (`src/decafclaw/eval/diagnostics.py`)**:
   - Added `"admin_read": "path"` to `READ_TOOL_ARGS`.
5. **Documentation**:
   - Added `Admin Files` section to `docs/tools.md`.
   - Updated `CLAUDE.md` / `AGENTS.md` key tools mapping.

## Verification & Testing
- 29 unit tests authored in `tests/test_admin_tools.py` covering path containment, workspace exclusion, read/list, write/replace/edit/delete mutations with approval and denial, unattended blocking, child agent blocking, and delegation isolation.
- `ruff check`, `ruff format`, and `pyright` passed with 0 errors and 0 warnings.
