"""Tool-output reading — paged access to saved tool output by handle (#1128)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..media import ToolResult
from ..tool_outputs import CAPPED, DEFAULT_PAGE_BYTES, DENIED, FAILED, MISSING, read_page

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# A page is bounded so one call cannot pour a whole artifact back into context.
MAX_PAGE_BYTES = 64 * 1024


def _page_text(handle: str, page) -> str:
    if page.state == DENIED:
        return f"[error: {handle} belongs to another conversation; its content is not readable from this conversation]"
    if page.state == MISSING:
        return (
            f"[error: no saved output for {handle}. The artifact may have been deleted, "
            "or the tool result was never large enough to be saved.]"
        )
    if page.state == FAILED:
        return f"[error: saved output for {handle} is unreadable: {page.message}]"

    parts = [page.content]
    if page.next_offset is not None:
        parts.append(
            f"[page: bytes {page.offset}-{page.next_offset} of {page.stored_bytes} stored. "
            f"Call again with offset={page.next_offset}.]"
        )
    elif page.state == CAPPED:
        parts.append(
            f"[saved artifact holds the first {page.stored_bytes} of {page.total_bytes} bytes; "
            "the rest was not captured when the output was truncated.]"
        )
    return "\n\n".join(part for part in parts if part)


async def tool_output_read(
    ctx: "Context",
    handle: str,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_BYTES,
) -> ToolResult:
    """Read one page of a saved tool output by its handle.

    The handle appears in the note appended to a truncated tool result, for
    example ``tool_output://<conv_id>/<tool_call_id>.txt``. Reads are scoped to
    the current conversation: a handle that names another conversation is
    refused rather than followed.
    """
    bounded = min(max(int(limit), 1), MAX_PAGE_BYTES)
    try:
        page = read_page(ctx.config, ctx.conv_id, handle, offset=int(offset), limit=bounded)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    text = _page_text(handle, page)
    if page.state in (DENIED, MISSING, FAILED):
        return ToolResult(text=text)
    return ToolResult(
        text=text,
        data={
            "state": page.state,
            "offset": page.offset,
            "next_offset": page.next_offset,
            "total_bytes": page.total_bytes,
            "stored_bytes": page.stored_bytes,
        },
    )


TOOL_OUTPUT_TOOLS = {
    "tool_output_read": tool_output_read,
}

TOOL_OUTPUT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "tool_output_read",
            "description": (
                "Read a saved tool output by its handle, one page at a time. "
                "When a tool result is too large, its body is saved and the result carries a note with a "
                "handle like `tool_output://<conversation>/<tool_call_id>.txt` — pass that handle here "
                "to read the saved content after it was truncated or cleared from context.\n\n"
                "Reads are scoped to the current conversation. States are reported distinctly: "
                "'complete' (full body saved), 'capped' (only the stored prefix exists), 'failed' "
                "(saving errored), 'missing' (artifact deleted or never materialized), and 'denied' "
                "(handle names another conversation). Use `offset` from `next_offset` to page forward."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "The tool-output handle from a truncation note, e.g. tool_output://abc123/call_456.txt",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Byte offset to start from. Default 0; use next_offset from a prior page.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "(bytes) Page size in bytes. Default 16384, capped at 65536.",
                    },
                },
                "required": ["handle"],
            },
        },
    }
]
