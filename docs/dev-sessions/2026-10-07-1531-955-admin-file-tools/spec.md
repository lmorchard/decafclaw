# Issue 955 Spec: Confirmation-Gated Admin File Tools

## Objective

Provide dedicated, confirmation-gated file operations (`admin_read`, `admin_list`, `admin_write`, `admin_replace_lines`, `admin_edit`, `admin_delete`) for the agent's administrative directory (`config.agent_path`).

## Background & Problem

Currently, all `workspace_*` tools enforce containment inside `config.workspace_path`. Files in the administrative tier outside the workspace (`config.agent_path`), such as:
- Admin-managed skills (`data/{agent_id}/skills/`)
- Prompts and templates (`data/{agent_id}/prompts/`)
- Configuration (`config.json`)
- Admin schedules (`data/{agent_id}/schedules/`)
- Admin heartbeat (`data/{agent_id}/HEARTBEAT.md`)

cannot be read or edited via workspace tools. Agents previously had to resort to raw `shell` commands (`cat`, `cp`, `sed`), which:
1. Lack structured unified diff previews in confirmation dialogs.
2. Introduce shell escaping and quoting hazards.
3. Degrade user visibility into administrative changes.

## Security Architecture & Invariants

1. **Strict Root Containment**:
   - All `admin_*` operations resolve relative to `config.agent_path`.
   - Any path escaping `config.agent_path` via `..` or absolute paths is rejected with `[error: path '{path}' is outside the agent directory]`.

2. **Workspace Exclusion**:
   - The agent workspace (`config.workspace_path`) lives under `config.agent_path / "workspace"`.
   - `admin_*` tools must reject any target resolving inside `config.workspace_path` with `[error: path '{path}' is inside the workspace; use workspace_* tools instead]`.
   - Root `.` for `admin_list` is allowed, listing top-level directories and files.

3. **Mandatory Interactive Confirmation for Mutations**:
   - Mutations (`admin_write`, `admin_replace_lines`, `admin_edit`, `admin_delete`) require interactive user confirmation via `request_confirmation`.
   - The confirmation card displays a structured unified diff preview of the changes against the existing file (or `(new file)` notice if creating, or delete notice if deleting).
   - `approve_label="Approve"` and `deny_label="Deny"` are passed so that "Always" or wildcard auto-approval buttons are suppressed in the UI; every administrative mutation requires explicit per-turn approval.

4. **Categorical Unattended & Child Blocking**:
   - If `ctx.is_unattended` or `ctx.is_child`:
     Mutations are immediately rejected with `[error: admin file mutations require interactive user confirmation; not available on unattended turns or child agents]`.
   - Child agents spawned via `delegate_task` / `delegate_tasks` have `_ADMIN_WRITE_TOOLS` removed from their allowed tools in `src/decafclaw/tools/delegate.py`.

5. **Tool Deferral & Discoverability**:
   - Registered with `priority: "normal"`, appearing in deferred tools and searchable via `tool_search`.
   - Fully callable by primary interactive agents.

## Tool Definitions

1. `admin_read(path: str, start_line: int | None = None, end_line: int | None = None)`
   - Read a file from `config.agent_path`.
   - Supports line range formatting and line capping (max 200 lines by default, mirroring `workspace_read`).

2. `admin_list(path: str = ".")`
   - List files and directories in `config.agent_path` or a subfolder (e.g. `skills`, `prompts`, `schedules`).
   - Suffixes directories with `/` and reports file sizes in bytes.

3. `admin_write(path: str, content: str)`
   - Create or overwrite a file in `config.agent_path`.
   - Computes diff against existing content (if any).
   - Interactive confirmation required with diff preview.

4. `admin_replace_lines(path: str, start_line: int, end_line: int, content: str = "")`
   - Replace a range of lines (1-based, inclusive) in an admin file. Empty content deletes lines.
   - Computes diff. Interactive confirmation required.

5. `admin_edit(path: str, old_text: str, new_text: str, replace_all: bool = False)`
   - Exact string replacement in an admin file.
   - Computes diff. Interactive confirmation required.

6. `admin_delete(path: str, recursive: bool = False)`
   - Delete an admin file or directory.
   - Interactive confirmation required.
   - Prevents deletion of `config.agent_path` root or `config.workspace_path`.
