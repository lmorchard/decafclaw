"""Workspace file tools — sandboxed to the agent's workspace directory."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..media import ToolResult

log = logging.getLogger(__name__)


def _resolve_safe(config, path_str: str) -> Path | None:
    """Resolve a path within the workspace, rejecting escapes."""
    workspace = config.workspace_path.resolve()
    target = (workspace / path_str).resolve()
    if not str(target).startswith(str(workspace)):
        return None
    return target


def tool_workspace_read(ctx, path: str, start_line: int | None = None,
                        end_line: int | None = None) -> str:
    """Read a file from the agent's workspace, optionally a line range."""
    log.info(f"[tool:workspace_read] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    try:
        content = resolved.read_text()
    except FileNotFoundError:
        return f"[error: file not found: {path}]"
    except IsADirectoryError:
        return f"[error: '{path}' is a directory, not a file]"
    except PermissionError:
        return f"[error: permission denied: {path}]"

    all_lines = content.splitlines()
    total = len(all_lines)
    # Determine range (1-based, inclusive)
    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    selected = all_lines[start - 1:end]
    width = len(str(end))
    numbered = [f"{str(start + i).rjust(width)}| {line}"
                for i, line in enumerate(selected)]
    partial = start_line is not None or end_line is not None
    if partial:
        header = f"Lines {start}-{end} of {total}:\n"
        return header + "\n".join(numbered)
    return "\n".join(numbered)


def tool_workspace_write(ctx, path: str, content: str) -> str:
    """Write content to a file in the agent's workspace."""
    log.info(f"[tool:workspace_write] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)
        return f"Wrote {len(content)} characters to {path}"
    except PermissionError:
        return f"[error: permission denied: {path}]"


def tool_workspace_list(ctx, path: str = ".") -> str:
    """List files and directories in the agent's workspace."""
    log.info(f"[tool:workspace_list] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    if not resolved.exists():
        return f"[error: path not found: {path}]"
    if not resolved.is_dir():
        return f"[error: '{path}' is not a directory]"
    try:
        entries = sorted(resolved.iterdir())
        lines = []
        for entry in entries:
            rel = entry.relative_to(resolved)
            suffix = "/" if entry.is_dir() else ""
            size = f" ({entry.stat().st_size}B)" if entry.is_file() else ""
            lines.append(f"{rel}{suffix}{size}")
        return "\n".join(lines) if lines else "(empty directory)"
    except PermissionError:
        return f"[error: permission denied: {path}]"


def tool_file_share(ctx, path: str, message: str = "") -> "ToolResult":
    """Share a file from the workspace as an attachment."""
    import mimetypes

    from ..media import ToolResult

    log.info(f"[tool:file_share] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")
    if not resolved.exists():
        return ToolResult(text=f"[error: file not found: {path}]")
    if resolved.is_dir():
        return ToolResult(text=f"[error: '{path}' is a directory, not a file]")

    try:
        data = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return ToolResult(
            text=message or f"Sharing {path}",
            media=[{
                "type": "file",
                "filename": resolved.name,
                "data": data,
                "content_type": content_type,
            }],
        )
    except PermissionError:
        return ToolResult(text=f"[error: permission denied: {path}]")


def tool_workspace_edit(ctx, path: str, old_text: str, new_text: str,
                       replace_all: bool = False) -> str:
    """Edit a file by replacing exact text matches."""
    log.info(f"[tool:workspace_edit] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    try:
        content = resolved.read_text()
    except FileNotFoundError:
        return f"[error: file not found: {path}]"
    except PermissionError:
        return f"[error: permission denied: {path}]"

    count = content.count(old_text)
    if count == 0:
        return (f"[error: text not found in {path}. "
                "Make sure old_text matches exactly, including whitespace and indentation.]")
    if count > 1 and not replace_all:
        return (f"[error: found {count} matches in {path}. "
                "Use replace_all=true for bulk replacement, "
                "or provide more surrounding context to make old_text unique.]")

    if replace_all:
        new_content = content.replace(old_text, new_text)
    else:
        new_content = content.replace(old_text, new_text, 1)
    resolved.write_text(new_content)
    return f"Edited {path}: replaced {count} occurrence(s)"


def tool_workspace_append(ctx, path: str, content: str) -> str:
    """Append content to a file in the agent's workspace."""
    log.info(f"[tool:workspace_append] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if resolved.exists():
            existing = resolved.read_text()
            if existing and not existing.endswith("\n"):
                content = "\n" + content
            resolved.write_text(existing + content)
        else:
            resolved.write_text(content)
        return f"Appended {len(content)} characters to {path}"
    except PermissionError:
        return f"[error: permission denied: {path}]"


WORKSPACE_TOOLS = {
    "workspace_read": tool_workspace_read,
    "workspace_write": tool_workspace_write,
    "workspace_list": tool_workspace_list,
    "file_share": tool_file_share,
    "workspace_append": tool_workspace_append,
    "workspace_edit": tool_workspace_edit,
}

WORKSPACE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "description": "Read a file from your workspace. Returns content with line numbers. Optionally read a specific line range with start_line/end_line (1-based, inclusive). Paths are relative to the workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
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
        "function": {
            "name": "workspace_write",
            "description": "Write content to a file in your workspace. Creates parent directories as needed. Paths are relative to the workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_append",
            "description": "Append content to the end of a file in your workspace. Creates the file (and parent directories) if it doesn't exist. Adds a newline separator if the file doesn't end with one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_edit",
            "description": "Edit a file by exact string replacement. Finds old_text and replaces it with new_text. Fails if old_text is not found or matches multiple locations (ambiguous) — provide more surrounding context to make it unique, or set replace_all=true for intentional bulk replacement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must match including whitespace and indentation)",
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
        "function": {
            "name": "file_share",
            "description": "Share a file from the workspace as an attachment in the conversation. The file will be uploaded and displayed inline (images) or as a download (other files). Use this to share reports, images, logs, or any workspace file with the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional message to include with the file",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_list",
            "description": "List files and directories in your workspace. Paths are relative to the workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (default: workspace root)",
                    },
                },
                "required": [],
            },
        },
    },
]
