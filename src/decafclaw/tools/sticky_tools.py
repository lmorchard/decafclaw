"""Agent-facing sticky-slot tools.

Pin a single display-only widget above the chat input, or clear it. The slot
is single-occupancy — pinning replaces any previous widget. Workflow-driven
producers (e.g. the checklist in #414) call decafclaw.sticky.set_sticky
directly; these tools are the agent's explicit surface.
"""

import logging

from .. import sticky as sticky_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


async def tool_widget_pin_sticky(ctx, widget_type: str, data: dict) -> ToolResult:
    """Pin a widget into the sticky slot above the chat input."""
    log.info("[tool:widget_pin_sticky] widget=%s", widget_type)
    result = await sticky_mod.set_sticky(
        ctx.config, ctx.conv_id, widget_type, data, emit=_emit_for_ctx(ctx))
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_widget_unpin_sticky(ctx) -> ToolResult:
    """Clear the sticky slot."""
    log.info("[tool:widget_unpin_sticky]")
    result = await sticky_mod.clear_sticky(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx))
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


STICKY_TOOLS = {
    "widget_pin_sticky": tool_widget_pin_sticky,
    "widget_unpin_sticky": tool_widget_unpin_sticky,
}

STICKY_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "widget_pin_sticky",
            "description": (
                "Pin a single display-only widget into the sticky slot directly "
                "above the chat input, where it stays visible while a workflow is "
                "in progress (unlike inline widgets, which scroll away). The slot "
                "holds ONE widget — pinning replaces any previous one. Use for "
                "at-a-glance status the user should keep seeing. Clear it with "
                "widget_unpin_sticky when the work is done. The widget_type must "
                "declare sticky mode (e.g. 'markdown_document')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {
                        "type": "string",
                        "description": "Registered sticky-mode widget name.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Widget payload; must conform to the widget's data_schema.",
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
            "name": "widget_unpin_sticky",
            "description": (
                "Clear the sticky slot above the chat input, hiding whatever "
                "widget was pinned there. Use when the pinned status is no longer "
                "relevant (e.g. the workflow finished)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
