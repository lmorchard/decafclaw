"""Agent-facing canvas tools — tab-aware (Phase 4).

Five tools that operate on explicit tab IDs. canvas_new_tab returns an
auto-generated tab_id; subsequent canvas_update / canvas_close_tab
target by id. canvas_clear nukes everything; canvas_read returns the
full state for grounding.
"""

import logging
from urllib.parse import quote

from .. import canvas as canvas_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


def _canvas_url(conv_id: str, tab_id: str | None = None) -> str:
    base = f"/canvas/{quote(conv_id, safe='')}"
    if tab_id:
        return f"{base}/{quote(tab_id, safe='')}"
    return base


async def tool_canvas_new_tab(ctx,
                              widget_type: str,
                              data: dict,
                              label: str | None = None) -> ToolResult:
    """Create a new canvas tab and make it active."""
    log.info("[tool:canvas_new_tab] widget=%s label=%r", widget_type, label)
    result = await canvas_mod.new_tab(
        ctx.config, ctx.conv_id, widget_type, data,
        label=label, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    url = _canvas_url(ctx.conv_id, result.tab_id)
    return ToolResult(
        text=f"tab created (id={result.tab_id}) — view at {url}",
        data={"tab_id": result.tab_id},
    )


async def tool_canvas_update(ctx, tab_id: str, data: dict) -> ToolResult:
    """Replace data of an existing tab. Preserves widget_type + label."""
    log.info("[tool:canvas_update] tab=%s", tab_id)
    result = await canvas_mod.update_tab(
        ctx.config, ctx.conv_id, tab_id, data, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_close_tab(ctx, tab_id: str) -> ToolResult:
    """Close a single tab by id. If it was active, the panel switches or hides."""
    log.info("[tool:canvas_close_tab] tab=%s", tab_id)
    result = await canvas_mod.close_tab(
        ctx.config, ctx.conv_id, tab_id, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_clear(ctx) -> ToolResult:
    """Close all canvas tabs and hide the panel."""
    log.info("[tool:canvas_clear]")
    state = canvas_mod.read_canvas_state(ctx.config, ctx.conv_id)
    if not state.get("tabs"):
        return ToolResult(text="canvas already empty")
    # Reuse canvas_mod.clear_canvas (existing) — emits kind="clear".
    result = await canvas_mod.clear_canvas(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_read(ctx) -> ToolResult:
    """Return the full canvas state including all tabs and active_tab."""
    log.info("[tool:canvas_read]")
    state = canvas_mod.read_canvas_state(ctx.config, ctx.conv_id)
    payload = {
        "active_tab": state.get("active_tab"),
        "tabs": [
            {
                "id": t["id"],
                "label": t.get("label", ""),
                "widget_type": t["widget_type"],
                "data": t.get("data", {}),
            }
            for t in state.get("tabs", [])
        ],
    }
    if not payload["tabs"]:
        text = "canvas is empty (no tabs)"
    else:
        labels = ", ".join(f"{t['id']}({t['label']})" for t in payload["tabs"])
        text = f"canvas has {len(payload['tabs'])} tab(s): {labels}; active={payload['active_tab']}"
    return ToolResult(text=text, data=payload)


CANVAS_TOOLS = {
    "canvas_new_tab": tool_canvas_new_tab,
    "canvas_update": tool_canvas_update,
    "canvas_close_tab": tool_canvas_close_tab,
    "canvas_clear": tool_canvas_clear,
    "canvas_read": tool_canvas_read,
}


CANVAS_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_new_tab",
            "description": (
                "Create a new tab on the conversation's canvas and make it the "
                "active tab. The canvas is a persistent display surface in the "
                "user's web UI — use it for documents, plans, or visualizations "
                "you intend to revise across multiple turns. Returns a tab_id "
                "you MUST keep to target this tab in subsequent canvas_update "
                "or canvas_close_tab calls. Currently supports widget_type='markdown_document' "
                "with data={content: <markdown>}, widget_type='code_block' "
                "with data={code: <string>, language?: <string>, filename?: <string>}, "
                "and widget_type='iframe_sandbox' with data={body: <html>, title?: <string>} "
                "for arbitrary HTML/CSS/JS demos in a CSP-locked sandboxed iframe "
                "(no network access — fetch, external scripts, remote images/fonts all blocked; "
                "inline <style> and <script> are allowed)."
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
                        "description": "Optional tab label. Defaults to first H1 of content for markdown_document, filename for code_block, else humanized widget_type.",
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
                "Replace the data of an existing canvas tab. Pass the tab_id "
                "you got from canvas_new_tab. Preserves widget_type and label. "
                "Use for revising a document — the panel updates without "
                "re-mounting the widget; scroll position is preserved. Errors "
                "if tab_id doesn't exist (use canvas_read to list current tabs)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "Tab id from canvas_new_tab (e.g. 'canvas_2').",
                    },
                    "data": {
                        "type": "object",
                        "description": "New data payload; must match the tab's widget data_schema.",
                    },
                },
                "required": ["tab_id", "data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_close_tab",
            "description": (
                "Close a single canvas tab by id. If it was the active tab, "
                "the panel switches to the left neighbor (else right; else "
                "hides). To replace a tab with a different widget_type, "
                "canvas_close_tab the old one and canvas_new_tab the new one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "Tab id to close.",
                    },
                },
                "required": ["tab_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_clear",
            "description": (
                "Close ALL canvas tabs and hide the panel. Use as a 'reset' "
                "when you're done with the canvas entirely. To close one tab, "
                "use canvas_close_tab instead."
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
                "Return the current canvas state — list of tabs (with id, "
                "label, widget_type, data) and the active_tab id. Use to "
                "ground revisions in current canvas state, especially after "
                "compaction or when you've lost track of tab ids."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
