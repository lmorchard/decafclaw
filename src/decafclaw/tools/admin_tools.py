"""Admin file tools — confirmation-gated file operations for the agent's data directory.

Rooted strictly at config.agent_path (~/.decafclaw/{agent_id}/).
Excludes config.workspace_path (use workspace_* tools for workspace files).
All mutations require interactive user confirmation with a unified diff preview.
Categorically blocked on unattended runs and child agents.
"""

from __future__ import annotations

import difflib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..media import ToolResult
from .confirmation import request_confirmation
from .file_locks import file_lock

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Max lines returned by admin_read when no line range is specified
MAX_READ_LINES = 200
# Context lines shown in diffs
DIFF_CONTEXT_LINES = 3


def _is_cancelled(ctx: "Context | None") -> bool:
    """Return True if the context has an active cancellation event set."""
    if ctx is None:
        return False
    cancelled = getattr(ctx, "cancelled", None)
    return bool(cancelled is not None and cancelled.is_set())


def _file_error(e: Exception, path: str) -> ToolResult:
    """Convert common file exceptions to a ToolResult error."""
    if isinstance(e, FileNotFoundError):
        return ToolResult(text=f"[error: file not found: {path}]")
    if isinstance(e, IsADirectoryError):
        return ToolResult(text=f"[error: path is a directory, not a file: {path}]")
    if isinstance(e, PermissionError):
        return ToolResult(text=f"[error: permission denied: {path}]")
    if isinstance(e, UnicodeDecodeError):
        return ToolResult(text=f"[error: file is not valid UTF-8 text: {path}]")
    return ToolResult(text=f"[error: {e}: {path}]")


def _mini_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Generate a compact unified diff for mutation previews and outputs."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}" if path else "before",
            tofile=f"b/{path}" if path else "after",
            n=DIFF_CONTEXT_LINES,
        )
    )
    if not diff:
        return ""
    return "".join(diff)


def _resolve_admin_path(config, path_str: str, *, allow_root: bool = False) -> tuple[Path | None, str | None]:
    """Resolve a path within the agent admin directory, rejecting escapes and workspace paths.

    Returns (resolved_path, error_message).
    If valid, error_message is None.
    If invalid, resolved_path is None and error_message explains why.
    """
    agent_dir = config.agent_path.resolve()
    cleaned = path_str.strip()
    if not cleaned or cleaned == ".":
        if allow_root:
            return agent_dir, None
        return None, f"[error: path '{path_str}' refers to the agent directory root, not a file]"

    target = (agent_dir / cleaned).resolve()
    if not target.is_relative_to(agent_dir):
        return None, f"[error: path '{path_str}' is outside the agent directory]"

    workspace_dir = config.workspace_path.resolve()
    if target == workspace_dir or target.is_relative_to(workspace_dir):
        return None, f"[error: path '{path_str}' is inside the workspace; use workspace_* tools instead]"

    return target, None


async def _confirm_admin_mutation(
    ctx: "Context",
    tool_name: str,
    path_str: str,
    preview: str,
) -> ToolResult | None:
    """Run interactive confirmation for an admin mutation.

    Returns ToolResult with error if blocked or denied, or None if approved.
    """
    if ctx.is_unattended or getattr(ctx, "is_child", False):
        return ToolResult(
            text=(
                f"[error: {tool_name} requires interactive user confirmation; "
                "not available on unattended turns or child agents]"
            )
        )

    if ctx.request_confirmation is None:
        return ToolResult(
            text=(f"[error: {tool_name} requires interactive user confirmation; not available from this context]")
        )

    command = f"{tool_name} '{path_str}'\n\n{preview}"
    approval = await request_confirmation(
        ctx,
        tool_name=tool_name,
        command=command,
        message=preview,
        force=True,
        approve_label="Approve",
        deny_label="Deny",
    )
    if not approval.get("approved"):
        return ToolResult(text=f"[error: {tool_name} for '{path_str}' was denied by user]")
    return None


def tool_admin_read(
    ctx: "Context",
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str | ToolResult:
    """Read a file from the agent admin directory (config.agent_path)."""
    log.info(f"[tool:admin_read] {path}")
    resolved, err = _resolve_admin_path(ctx.config, path)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: path is a directory, not a file: {path}]")

    try:
        content = resolved.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError) as e:
        return _file_error(e, path)

    all_lines = content.splitlines()
    total = len(all_lines)
    partial = start_line is not None or end_line is not None
    base_data: dict = {
        "path": path,
        "size": len(content.encode("utf-8")),
        "lines": total,
    }

    if not partial and total > MAX_READ_LINES:
        end = MAX_READ_LINES
        selected = all_lines[:end]
        width = len(str(end))
        numbered = [f"{str(i + 1).rjust(width)}| {line}" for i, line in enumerate(selected)]
        header = (
            f"File has {total} lines, showing first {MAX_READ_LINES}. "
            f"Use start_line/end_line to read specific sections.\n"
        )
        return ToolResult(
            text=header + "\n".join(numbered),
            data={**base_data, "range": [1, end], "truncated": True},
        )

    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    selected = all_lines[start - 1 : end]
    width = len(str(end))
    numbered = [f"{str(start + i).rjust(width)}| {line}" for i, line in enumerate(selected)]
    if partial:
        header = f"Lines {start}-{end} of {total}:\n"
        return ToolResult(
            text=header + "\n".join(numbered),
            data={**base_data, "range": [start, end], "truncated": False},
        )
    return ToolResult(
        text="\n".join(numbered),
        data={**base_data, "range": [1, total], "truncated": False},
    )


def tool_admin_list(ctx: "Context", path: str = ".") -> str | ToolResult:
    """List files and directories in the agent admin directory (config.agent_path)."""
    log.info(f"[tool:admin_list] {path}")
    resolved, err = _resolve_admin_path(ctx.config, path, allow_root=True)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if not resolved.exists():
        return ToolResult(text=f"[error: path not found: {path}]")
    if not resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is not a directory]")

    try:
        entries = sorted(resolved.iterdir())
        lines = []
        data_entries: list[dict] = []
        for entry in entries:
            rel = entry.relative_to(resolved)
            is_dir = entry.is_dir()
            suffix = "/" if is_dir else ""
            size_bytes = entry.stat().st_size if entry.is_file() else None
            size = f" ({size_bytes}B)" if size_bytes is not None else ""
            lines.append(f"{rel}{suffix}{size}")
            data_entries.append(
                {
                    "name": str(rel),
                    "is_dir": is_dir,
                    "size": size_bytes,
                }
            )
        text = "\n".join(lines) if lines else "(empty directory)"
        return ToolResult(
            text=text,
            data={"path": path, "entries": data_entries},
        )
    except PermissionError as e:
        return _file_error(e, path)


async def tool_admin_write(ctx: "Context", path: str, content: str) -> str | ToolResult:
    """Create or overwrite a file in the agent admin directory."""
    log.info(f"[tool:admin_write] {path}")
    resolved, err = _resolve_admin_path(ctx.config, path)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory, not a file]")

    existing = ""
    exists = resolved.exists()
    if exists:
        try:
            existing = resolved.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError) as e:
            return _file_error(e, path)

    if exists:
        diff = _mini_diff(existing, content, path)
        preview = diff if diff else "(no changes)"
    else:
        preview = f"(new file, {len(content)} characters)\n\n" + (
            content[:500] + ("\n..." if len(content) > 500 else "")
        )

    gate = await _confirm_admin_mutation(ctx, "admin_write", path, preview)
    if gate is not None:
        return gate

    with file_lock(resolved):
        if _is_cancelled(ctx):
            return ToolResult(text="[tool interrupted: agent turn cancelled]")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            summary = f"Wrote {len(content)} characters to admin file '{path}'"
            if exists and diff:
                return f"{summary}\n\n{diff}"
            return summary
        except PermissionError as e:
            return _file_error(e, path)


async def tool_admin_replace_lines(
    ctx: "Context", path: str, start_line: int, end_line: int, content: str = ""
) -> str | ToolResult:
    """Replace a range of lines in an admin file. Pass empty content to delete lines."""
    log.info(f"[tool:admin_replace_lines] {path} lines {start_line}-{end_line}")
    resolved, err = _resolve_admin_path(ctx.config, path)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory, not a file]")

    try:
        existing = resolved.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError) as e:
        return _file_error(e, path)

    lines = existing.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        return ToolResult(text=f"[error: invalid line range {start_line}-{end_line}. File has {len(lines)} lines.]")

    if content:
        if not content.endswith("\n"):
            content += "\n"
        replacement = content.splitlines(keepends=True)
    else:
        replacement = []

    new_lines = list(lines)
    new_lines[start_line - 1 : end_line] = replacement
    new_content = "".join(new_lines)
    diff = _mini_diff(existing, new_content, path)

    gate = await _confirm_admin_mutation(ctx, "admin_replace_lines", path, diff or "(no changes)")
    if gate is not None:
        return gate

    with file_lock(resolved):
        if _is_cancelled(ctx):
            return ToolResult(text="[tool interrupted: agent turn cancelled]")
        try:
            resolved.write_text(new_content, encoding="utf-8")
            if not content:
                summary = f"Deleted lines {start_line}-{end_line} from admin file '{path}'"
            else:
                summary = (
                    f"Replaced lines {start_line}-{end_line} with {len(replacement)} line(s) in admin file '{path}'"
                )
            if diff:
                return f"{summary}\n\n{diff}"
            return summary
        except PermissionError as e:
            return _file_error(e, path)


async def tool_admin_edit(
    ctx: "Context", path: str, old_text: str, new_text: str, replace_all: bool = False
) -> str | ToolResult:
    """Edit an admin file by replacing exact text matches."""
    log.info(f"[tool:admin_edit] {path}")
    resolved, err = _resolve_admin_path(ctx.config, path)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory, not a file]")

    try:
        content = resolved.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError) as e:
        return _file_error(e, path)

    count = content.count(old_text)
    if count == 0:
        return ToolResult(
            text=f"[error: text not found in {path}. "
            "Make sure old_text matches exactly, including whitespace and indentation.]"
        )
    if count > 1 and not replace_all:
        return ToolResult(
            text=f"[error: found {count} matches in {path}. "
            "Use replace_all=true for bulk replacement, "
            "or provide more surrounding context to make old_text unique.]"
        )

    if replace_all:
        new_content = content.replace(old_text, new_text)
    else:
        new_content = content.replace(old_text, new_text, 1)

    diff = _mini_diff(content, new_content, path)
    gate = await _confirm_admin_mutation(ctx, "admin_edit", path, diff or "(no changes)")
    if gate is not None:
        return gate

    with file_lock(resolved):
        if _is_cancelled(ctx):
            return ToolResult(text="[tool interrupted: agent turn cancelled]")
        try:
            resolved.write_text(new_content, encoding="utf-8")
            summary = f"Edited admin file '{path}': replaced {count} occurrence(s)"
            if diff:
                return f"{summary}\n\n{diff}"
            return summary
        except PermissionError as e:
            return _file_error(e, path)


async def tool_admin_delete(ctx: "Context", path: str, recursive: bool = False) -> str | ToolResult:
    """Delete a file or directory in the agent admin directory."""
    log.info(f"[tool:admin_delete] {path} recursive={recursive}")
    resolved, err = _resolve_admin_path(ctx.config, path)
    if err:
        return ToolResult(text=err)
    assert resolved is not None

    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")

    if resolved.is_dir():
        is_empty = not any(resolved.iterdir())
        if not is_empty and not recursive:
            return ToolResult(
                text=f"[error: '{path}' is a directory. Set recursive=true to delete non-empty directories.]"
            )
        preview = f"Delete directory: {path} (recursive={recursive})"
    else:
        preview = f"Delete file: {path} ({resolved.stat().st_size} bytes)"

    gate = await _confirm_admin_mutation(ctx, "admin_delete", path, preview)
    if gate is not None:
        return gate

    with file_lock(resolved):
        if _is_cancelled(ctx):
            return ToolResult(text="[tool interrupted: agent turn cancelled]")
        try:
            if resolved.is_dir():
                if recursive:
                    shutil.rmtree(resolved)
                else:
                    resolved.rmdir()
                return f"Deleted admin directory '{path}'"
            else:
                resolved.unlink()
                return f"Deleted admin file '{path}'"
        except PermissionError as e:
            return _file_error(e, path)


ADMIN_TOOLS = {
    "admin_read": tool_admin_read,
    "admin_list": tool_admin_list,
    "admin_write": tool_admin_write,
    "admin_replace_lines": tool_admin_replace_lines,
    "admin_edit": tool_admin_edit,
    "admin_delete": tool_admin_delete,
}

ADMIN_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_read",
            "description": (
                "Read a file from the agent's administrative directory (config.agent_path) — "
                "e.g. admin skills, prompts, configuration, or admin schedules. "
                "NOT for workspace files (use workspace_read for those) or vault pages (use vault_read). "
                "Returns content with line numbers. Optionally read a specific line range with start_line/end_line (1-based, inclusive)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the agent admin directory (e.g. 'skills/my-skill/SKILL.md', 'config.json')",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, inclusive). Omit to start from beginning.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive). Omit to read to end of file.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_list",
            "description": (
                "List files and directories in the agent's administrative directory (config.agent_path) "
                "or a subfolder (e.g. 'skills', 'prompts', 'schedules'). Paths are relative to the agent admin directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (default: '.' for agent admin root)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_write",
            "description": (
                "Create or overwrite a file in the agent's administrative directory (config.agent_path) — "
                "e.g. admin skills ('skills/...'), prompts ('prompts/...'), or configuration ('config.json'). "
                "REQUIRES INTERACTIVE USER CONFIRMATION showing a unified diff preview. "
                "Blocked on unattended turns and child agents. NOT for workspace files (use workspace_write)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the agent admin directory (e.g. 'skills/my-skill/SKILL.md')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_replace_lines",
            "description": (
                "Replace a range of lines (1-based, inclusive) in an admin file under config.agent_path with new content. "
                "Pass empty content to delete lines. Use admin_read first to inspect line numbers. "
                "REQUIRES INTERACTIVE USER CONFIRMATION showing a unified diff preview. "
                "Blocked on unattended turns and child agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the agent admin directory",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to replace (1-based, inclusive)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to replace (1-based, inclusive)",
                    },
                    "content": {
                        "type": "string",
                        "description": "New content to replace the lines with (pass empty string to delete)",
                    },
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_edit",
            "description": (
                "Edit a file in the agent admin directory (config.agent_path) by replacing exact text matches. "
                "Use admin_read first fresh from disk. "
                "REQUIRES INTERACTIVE USER CONFIRMATION showing a unified diff preview. "
                "Blocked on unattended turns and child agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the agent admin directory",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text currently in the file",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to replace old_text with",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "admin_delete",
            "description": (
                "Delete a file or directory in the agent's administrative directory (config.agent_path). "
                "REQUIRES INTERACTIVE USER CONFIRMATION. Blocked on unattended turns and child agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of file or directory within the agent admin directory to delete",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, delete directory and all its contents recursively (default: false)",
                    },
                },
                "required": ["path"],
            },
        },
    },
]
