# File Staging Between Parent and Child — Spec

## Goal

Add file transfer tools to the Claude Code skill so the parent agent can push files into a session's working directory and pull files back out. Reduces ad-hoc path juggling for multi-step workflows (specs in, artifacts out).

Covers issue: #211 (file staging). Part of umbrella #213.

## 1. New tools

### `claude_code_push_file(session_id, source_path, dest_name)`

Copy a file from the parent's workspace into the session's cwd.

**Parameters:**
- `session_id` (required) — active session
- `source_path` (required) — path relative to the parent's workspace
- `dest_name` (optional) — filename or relative path within the session's cwd. Defaults to the basename of `source_path`.

**Behavior:**
- Resolve `source_path` relative to the workspace, enforce sandbox (must be within workspace)
- Resolve `dest_name` relative to the session's cwd
- Create parent directories for `dest_name` if needed
- Copy using `shutil.copy2` (preserves metadata, handles binary)
- Touch session (update last_active)

**Returns:** `ToolResult` with `data`:
```python
{
    "status": "success",
    "source": str,      # resolved source path
    "dest": str,        # resolved dest path
    "size_bytes": int,  # file size after copy
}
```

### `claude_code_pull_file(session_id, source_name, dest_path)`

Copy a file from the session's cwd to the parent's workspace.

**Parameters:**
- `session_id` (required) — active session
- `source_name` (required) — filename or relative path within the session's cwd
- `dest_path` (optional) — path relative to the parent's workspace. Defaults to the basename of `source_name`.

**Behavior:**
- Resolve `source_name` relative to the session's cwd
- Resolve `dest_path` relative to the workspace, enforce sandbox (must be within workspace)
- Create parent directories for `dest_path` if needed
- Copy using `shutil.copy2`
- Touch session (update last_active)

**Returns:** `ToolResult` with `data`:
```python
{
    "status": "success",
    "source": str,      # resolved source path
    "dest": str,        # resolved dest path
    "size_bytes": int,  # file size after copy
}
```

## 2. Design decisions

### Confirmation model
Auto-approved — no user confirmation needed. Both paths are sandboxed (workspace on one side, session cwd on the other), and the parent agent explicitly requests the copy.

### File types
Supports any file type (text, binary, images, build artifacts). Uses `shutil.copy2` which copies bytes transparently.

### Scope
Single files only. No directory support in initial implementation. The parent can push multiple files with multiple calls.

### Path traversal protection
Both push and pull enforce that resolved paths stay within their respective sandboxes:
- Push: `source_path` must resolve within workspace, `dest_name` must resolve within session cwd
- Pull: `source_name` must resolve within session cwd, `dest_path` must resolve within workspace
Use `Path.is_relative_to()` for checks (same pattern as `claude_code_start`).

### Overwrite behavior
If the destination file already exists, it is silently overwritten. This matches standard `shutil.copy2` behavior.

### Error handling
- Session not found/expired → error ToolResult
- Source file doesn't exist → error ToolResult
- Source is a directory → error ToolResult (files only)
- Path outside sandbox → error ToolResult
- Copy failure (permissions, disk space) → error ToolResult with exception message

## 3. Files changed

- `src/decafclaw/skills/claude_code/tools.py` — add two tool functions, TOOLS dict entries, TOOL_DEFINITIONS
- `src/decafclaw/skills/claude_code/SKILL.md` — document new tools
- Tests for push and pull (sandbox enforcement, missing files, happy path)

## 4. Out of scope

- Directory push/pull (may add later if needed)
- Streaming/chunked transfer for large files
- File diffing or content inspection
- Shared mount / symlink approach
