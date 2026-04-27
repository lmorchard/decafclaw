"""Agent-facing canvas tools — push, replace, clear, and read the canvas surface.

The canvas is a per-conversation, web-only display area where the agent
maintains a living widget across multiple turns. These four tools wrap
the internal state operations in :mod:`decafclaw.canvas` and project
results into ``ToolResult`` objects suitable for the agent loop.

All four tools are always-loaded (small definitions, low cost) and run
under the standard 180s tool timeout.
"""

import logging

from .. import canvas as canvas_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    """Build an emit callable from the conversation manager on ctx.

    Returns ``None`` when there's no manager (unit tests, terminal).
    canvas.py treats ``None`` as fail-open.
    """
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit  # async (conv_id, event)


def _canvas_url(conv_id: str) -> str:
    return f"/canvas/{conv_id}"


async def tool_canvas_set(ctx,
                          widget_type: str,
                          data: dict,
                          label: str | None = None) -> ToolResult:
    """Push a widget onto the canvas, replacing any existing tab."""
    log.info("[tool:canvas_set] widget=%s label=%r", widget_type, label)
    result = await canvas_mod.set_canvas(
        ctx.config, ctx.conv_id, widget_type, data,
        label=label, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=f"{result.text} — view at {_canvas_url(ctx.conv_id)}")


async def tool_canvas_update(ctx, data: dict) -> ToolResult:
    """Replace the data of the current canvas widget. Errors if none set."""
    log.info("[tool:canvas_update]")
    result = await canvas_mod.update_canvas(
        ctx.config, ctx.conv_id, data, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_clear(ctx) -> ToolResult:
    """Remove the canvas widget; hides the panel for all watchers."""
    log.info("[tool:canvas_clear]")
    result = await canvas_mod.clear_canvas(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx),
    )
    return ToolResult(text=result.text)


async def tool_canvas_read(ctx) -> ToolResult:
    """Return the current canvas tab as structured data, or null if empty."""
    log.info("[tool:canvas_read]")
    tab = canvas_mod.get_active_tab(ctx.config, ctx.conv_id)
    if tab is None:
        return ToolResult(text="canvas is empty (no widget set)", data=None)
    payload = {
        "widget_type": tab["widget_type"],
        "label": tab.get("label", ""),
        "data": tab.get("data", {}),
    }
    return ToolResult(
        text=f"current canvas: {payload['widget_type']} ({payload['label']})",
        data=payload,
    )


CANVAS_TOOLS = {
    "canvas_set": tool_canvas_set,
    "canvas_update": tool_canvas_update,
    "canvas_clear": tool_canvas_clear,
    "canvas_read": tool_canvas_read,
}


CANVAS_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_set",
            "description": (
                "Push a widget onto the conversation's canvas, replacing any "
                "existing widget. The canvas is a persistent display surface "
                "in the user's web UI — use it for documents, plans, or "
                "visualizations you intend to revise across multiple turns. "
                "Always reveals the panel to the user. Currently supports "
                "widget_type='markdown_document' with data={content: <markdown>}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {
                        "type": "string",
                        "description": "Registered canvas-mode widget name.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Widget payload; must conform to the widget's data_schema.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional tab label. Defaults to first H1 of content for markdown_document.",
                    },
                },
                "required": ["widget_type", "data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_update",
            "description": (
                "Replace the data of the existing canvas widget. Same "
                "widget_type, same label. Use for revising the current "
                "document — preserves scroll position and does NOT pop the "
                "panel back open if the user has dismissed it. Errors if no "
                "canvas_set has happened yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "description": "New data payload; must match the current widget's data_schema.",
                    },
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_clear",
            "description": (
                "Remove the canvas widget and hide the panel for all "
                "watchers. No-op if the canvas is already empty."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_read",
            "description": (
                "Return the current canvas widget as {widget_type, label, "
                "data}, or null if empty. Use to ground revisions in the "
                "current canvas state — especially after compaction or after "
                "the user clicks 'Open in Canvas' on an inline widget."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
