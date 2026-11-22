"""Agent-facing canvas tools — tab-aware (Phase 4).

Five tools that operate on explicit tab IDs. canvas_new_tab returns an
auto-generated tab_id; subsequent canvas_update / canvas_close_tab
target by id. canvas_clear nukes everything; canvas_read returns the
full state for grounding.
"""

import logging
from typing import TYPE_CHECKING
from urllib.parse import quote

from .. import canvas as canvas_mod
from ..events import emit_for_ctx
from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _canvas_url(conv_id: str, tab_id: str | None = None) -> str:
    base = f"/canvas/{quote(conv_id, safe='')}"
    if tab_id:
        return f"{base}/{quote(tab_id, safe='')}"
    return base


async def tool_canvas_new_tab(
    ctx: "Context",
    widget_type: str | None = None,
    data: dict | None = None,
    label: str | None = None,
    artifact_id: str | None = None,
    background: bool = False,
) -> ToolResult:
    """Create a new canvas tab (optionally in the background), or reopen an existing artifact."""
    if artifact_id:
        log.info("[tool:canvas_new_tab] reopen artifact=%s label=%r background=%s", artifact_id, label, background)
        reopened = await canvas_mod.reopen_artifact(
            ctx.config,
            ctx.conv_id,
            artifact_id,
            label=label,
            emit=emit_for_ctx(ctx),
            background=background,
        )
        if not reopened.ok:
            return ToolResult(text=f"[error: {reopened.error}]")
        url = _canvas_url(ctx.conv_id, reopened.tab_id)
        return ToolResult(
            text=(
                f"artifact {artifact_id} reopened as "
                f"{'background ' if background else ''}tab (id={reopened.tab_id})"
                f"{'; current view unchanged' if background else ''} — view at {url}"
            ),
            data={"tab_id": reopened.tab_id, "artifact_id": artifact_id},
        )
    if not widget_type or data is None:
        return ToolResult(text="[error: widget_type and data are required unless artifact_id is given]")
    log.info("[tool:canvas_new_tab] widget=%s label=%r background=%s", widget_type, label, background)
    result = await canvas_mod.new_tab(
        ctx.config,
        ctx.conv_id,
        widget_type,
        data,
        label=label,
        emit=emit_for_ctx(ctx),
        background=background,
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    url = _canvas_url(ctx.conv_id, result.tab_id)
    created = "tab prepared in background" if background else "tab created"
    # Report the durable handle so the tab can be reopened after it is closed.
    tab = canvas_mod.get_tab(ctx.config, ctx.conv_id, result.tab_id or "") or {}
    payload = {"tab_id": result.tab_id}
    if tab.get("artifact_id"):
        payload["artifact_id"] = tab["artifact_id"]
        return ToolResult(
            text=(
                f"{created} (id={result.tab_id}, artifact={tab['artifact_id']}) — view at {url}. "
                "Closing the tab hides it; the content stays and canvas_new_tab with "
                f"artifact_id='{tab['artifact_id']}' reopens it."
            ),
            data=payload,
        )
    return ToolResult(
        text=f"{created} (id={result.tab_id}) — view at {url}",
        data=payload,
    )


async def tool_canvas_update(ctx: "Context", tab_id: str, data: dict) -> ToolResult:
    """Replace data of an existing tab. Preserves widget_type + label."""
    log.info("[tool:canvas_update] tab=%s", tab_id)
    result = await canvas_mod.update_tab(
        ctx.config,
        ctx.conv_id,
        tab_id,
        data,
        emit=emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_close_tab(ctx: "Context", tab_id: str) -> ToolResult:
    """Close a single tab by id. If it was active, the panel switches or hides."""
    log.info("[tool:canvas_close_tab] tab=%s", tab_id)
    result = await canvas_mod.close_tab(
        ctx.config,
        ctx.conv_id,
        tab_id,
        emit=emit_for_ctx(ctx),
        registry=ctx.terminal_registry,
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_clear(ctx: "Context") -> ToolResult:
    """Close all canvas tabs and hide the panel."""
    log.info("[tool:canvas_clear]")
    state = canvas_mod.read_canvas_state(ctx.config, ctx.conv_id)
    if not state.get("tabs"):
        return ToolResult(text="canvas already empty")
    # Reuse canvas_mod.clear_canvas (existing) — emits kind="clear".
    result = await canvas_mod.clear_canvas(
        ctx.config,
        ctx.conv_id,
        emit=emit_for_ctx(ctx),
        registry=ctx.terminal_registry,
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_read(ctx: "Context") -> ToolResult:
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
                **({"artifact_id": t["artifact_id"]} if t.get("artifact_id") else {}),
            }
            for t in state.get("tabs", [])
        ],
        # Durable content handles, including ones whose tab was closed.
        "artifacts": canvas_mod.list_artifacts(ctx.config, ctx.conv_id),
    }
    if not payload["tabs"]:
        text = "canvas is empty (no tabs)"
    else:
        labels = ", ".join(f"{t['id']}({t['label']})" for t in payload["tabs"])
        text = f"canvas has {len(payload['tabs'])} tab(s): {labels}; active={payload['active_tab']}"
    if payload["artifacts"]:
        kept = ", ".join(f"{a['artifact_id']}({a['label'] or a['widget_type']})" for a in payload["artifacts"])
        text += f"; {len(payload['artifacts'])} artifact(s) kept: {kept}"
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
        "writes": True,
        "summary": "Open a new canvas tab and make it active.",
        "function": {
            "name": "canvas_new_tab",
            "description": (
                "Create a new tab on the conversation's canvas and make it the "
                "active tab. The canvas is a persistent display surface in the "
                "user's web UI — use it for documents, plans, visualizations, "
                "or interactive demos you intend to revise across multiple "
                "turns. Set background=true to prepare content WITHOUT "
                "interrupting the user: the current tab stays selected and a "
                "dismissed panel stays dismissed (the new tab is discoverable "
                "in the tab list). Use background=true when the user asks you "
                "to prepare, stage, or queue something while they read or work "
                "on something else. Returns a tab_id you MUST keep to target "
                "this tab in subsequent canvas_update or canvas_close_tab calls. "
                "A markdown_document also gets a durable artifact_id: closing its "
                "tab only hides it, and calling canvas_new_tab with that "
                "artifact_id (no widget_type/data) reopens the same content in a "
                "fresh tab — use canvas_read to list kept artifacts. "
                "Currently supports widget_type='markdown_document' "
                "with data={content: <markdown>}, widget_type='code_block' "
                "with data={code: <string>, language?: <string>, filename?: <string>}, "
                "widget_type='map' with data={markers: [{lat, lng, label?, popup?}], center?: {lat, lng}, zoom?: <int>, title?: <string>} "
                "for showing locations/places on an interactive geographic map, "
                "widget_type='web_frame' with data={url: <string>, title?: <string>} "
                "for embedding approved local, tailnet, or homelab companion web applications "
                "(Marimo notebooks, Television artifacts), "
                "and widget_type='iframe_sandbox' with data={body: <html>, title?: <string>} — "
                "PREFER this over workspace_write whenever the user asks you to 'show', "
                "'display', 'render', or 'demo' interactive HTML/CSS/JS content (3D demos, "
                "charts, visualizations, mini-apps, animations); it renders the page directly "
                "in their UI instead of producing a file they have to open. Runs in a "
                "CSP-locked sandboxed iframe (no network access — fetch, external scripts, "
                "remote images/fonts all blocked; inline <style> and <script> are allowed). "
                "For maps or anything needing the network (map tiles, external data), use "
                "widget_type='map' or another purpose-built widget — iframe_sandbox CANNOT "
                "load external scripts, stylesheets, images, fonts, or fetch from CDNs (e.g. "
                "unpkg); everything in it must be inline or a data: URI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {
                        "type": "string",
                        "description": "Registered canvas-mode widget name. Required unless artifact_id is given.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Widget payload; must conform to the widget's data_schema. Required unless artifact_id is given.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional tab label. Defaults to first H1 of content for markdown_document, filename for code_block, else humanized widget_type.",
                    },
                    "artifact_id": {
                        "type": "string",
                        "description": "Reopen an existing artifact (e.g. 'artifact_1' from canvas_read) in a fresh tab instead of creating content. Omit widget_type and data when using this.",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Create the tab without changing the current view: keep the current selection and a dismissed panel dismissed. Defaults to false (select the new tab and reveal the panel).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Replace the data of an existing canvas tab.",
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
        "writes": True,
        "summary": "Close one canvas tab by id.",
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
        "writes": True,
        "summary": "Close every canvas tab and hide the panel.",
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
        "writes": False,
        "summary": "Read the canvas tabs, their data, and the active tab id.",
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
