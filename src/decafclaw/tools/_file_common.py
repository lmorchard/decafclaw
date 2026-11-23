"""Shared helpers for file tools.

Owns the small set of helpers and constants shared by the workspace, admin,
and prompt file-tool modules (#1340): the cancellation check,
file-exception-to-error conversion, and read/diff size limits. Text patching
and unified diffs live in ``decafclaw.text_patching`` (#1771).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

# Max lines returned by read tools when no line range is specified
MAX_READ_LINES = 200
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
