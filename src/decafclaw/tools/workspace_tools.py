"""Workspace file tools — sandboxed to the agent's workspace directory."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _resolve_safe(config, path_str: str) -> Path | None:
    """Resolve a path within the workspace, rejecting escapes."""
    workspace = config.workspace_path.resolve()
    target = (workspace / path_str).resolve()
    if not str(target).startswith(str(workspace)):
        return None
    return target


def tool_workspace_read(ctx, path: str) -> str:
    """Read a file from the agent's workspace."""
    log.info(f"[tool:workspace_read] {path}")
    resolved = _resolve_safe(ctx.config, path)
    if resolved is None:
        return f"[error: path '{path}' is outside the workspace]"
    try:
        return resolved.read_text()
    except FileNotFoundError:
        return f"[error: file not found: {path}]"
    except IsADirectoryError:
        return f"[error: '{path}' is a directory, not a file]"
    except PermissionError:
        return f"[error: permission denied: {path}]"


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


WORKSPACE_TOOLS = {
    "workspace_read": tool_workspace_read,
    "workspace_write": tool_workspace_write,
    "workspace_list": tool_workspace_list,
}

WORKSPACE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "description": "Read a file from your workspace. Paths are relative to the workspace root. You cannot access files outside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
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
