"""Shared helpers for file tools.

Owns the small set of helpers and constants duplicated across the
workspace and admin file-tool modules (#1340): the cancellation check,
file-exception-to-error conversion, and compact unified diffs.
``workspace_tools`` imports from here; ``admin_tools`` keeps its own
copies until 6.4b adopts this module as well.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

# Max lines returned by read tools when no line range is specified
MAX_READ_LINES = 200
# Context lines shown in mini-diffs
DIFF_CONTEXT_LINES = 3
# Max characters per file buffer before diff_view widget attachment is suppressed
MAX_DIFF_WIDGET_CHARS = 50_000
# Max total lines before diff_view widget attachment is suppressed
MAX_DIFF_WIDGET_LINES = 1_000


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
    """Generate a compact unified diff for file-tool output."""
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
