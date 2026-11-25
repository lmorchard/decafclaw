"""Widget, canvas, and sticky resource routes — per-resource route module.

One ``web/<resource>_routes.py`` module holds a resource's request/response
models, its handlers, and an ``APIRouter`` that ties them together.
``http_server.create_app`` aggregates the routers; it no longer defines a
resource's routes inline.

Conventions for adding or editing a resource module:

- **Models + handlers + router live here.** ``http_server`` imports the
  router and extends its route list with it. Handlers read their dependencies
  off ``request.app.state`` (populated by ``create_app``) and take the
  authenticated username via :func:`decafclaw.web.http_auth._authenticated`.
- **Shared auth lives in ``http_auth``**, not here: the decorator is applied at
  module load, so it must be importable without a cycle back to ``http_server``.
  Cross-cutting helpers that several resources share stay in ``http_server``
  and are imported at call time.
- **Route order is part of the contract.** The generated OpenAPI spec lists
  paths in registration order, and ``make check`` fails on any drift. Register
  routes so the app's final order is unchanged: this router's routes land in
  their historical positions (see ``create_app``).

This module hosts three small resources that share the widget/canvas/sticky UI
surface:

- **Widgets** — the interactive tool-widget catalog (``/api/widgets``) and
  per-widget JavaScript (``/widgets/{tier}/{name}/widget.js``).
- **Canvas** — the per-conversation tab panel (``/api/canvas/{conv_id}/*``)
  and the standalone canvas page (``/canvas/{conv_id}``).
- **Sticky** — pins a widget into the conversation's sticky slot
  (``/api/sticky/{conv_id}``).
"""

import json
import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, JsonValue
from starlette.responses import FileResponse, JSONResponse, Response

from .http_auth import _authenticated, _get_username_or_401

log = logging.getLogger(__name__)


_SAFE_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_conv_id(conv_id: str) -> bool:
    return bool(conv_id and _SAFE_CONV_ID_RE.match(conv_id))


# -- Widget routes ------------------------------------------------------------


class WidgetDescriptorResponse(BaseModel):
    name: str
    tier: str
    description: str
    modes: list[str]
    accepts_input: bool
    data_schema: dict[str, JsonValue]
    js_url: str


class WidgetCatalogResponse(BaseModel):
    widgets: list[WidgetDescriptorResponse]


async def list_widgets(request: Request) -> JSONResponse:
    """Return the widget catalog with cache-busted js URLs."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from ..widgets import get_widget_registry

    registry = get_widget_registry()
    if registry is None:
        return JSONResponse({"widgets": []})
    out = []
    for d in registry.list():
        out.append(
            {
                "name": d.name,
                "tier": d.tier,
                "description": d.description,
                "modes": d.modes,
                "accepts_input": d.accepts_input,
                "data_schema": d.data_schema,
                "js_url": (f"/widgets/{d.tier}/{d.name}/widget.js?v={int(d.mtime * 1000)}"),
            }
        )
    return JSONResponse({"widgets": out})


@_authenticated
async def serve_widget_js(request: Request, username: str):
    """Serve widget.js for a registered widget.

    Tier in the URL must match the widget's actual tier so bundled and
    admin widgets of the same name don't leak across paths. The resolved
    js path is also confirmed to live under the expected tier root so a
    symlinked widget.js can't expose arbitrary files.
    """
    from ..widgets import get_widget_registry

    tier = request.path_params.get("tier", "")
    name = request.path_params.get("name", "")
    if tier not in ("bundled", "admin"):
        return JSONResponse({"error": "unknown tier"}, status_code=404)
    registry = get_widget_registry()
    if registry is None:
        return JSONResponse({"error": "registry unavailable"}, status_code=404)
    desc = registry.get(name)
    if desc is None or desc.tier != tier:
        return JSONResponse({"error": "widget not found"}, status_code=404)
    try:
        resolved = desc.js_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return JSONResponse({"error": "widget not found"}, status_code=404)
    try:
        resolved.relative_to(desc.tier_root)
    except ValueError:
        log.warning("widget %r resolves to %s outside tier root %s — refusing", name, resolved, desc.tier_root)
        return JSONResponse({"error": "widget not found"}, status_code=404)
    return FileResponse(
        str(resolved), media_type="application/javascript", headers={"X-Content-Type-Options": "nosniff"}
    )


# -- Canvas routes ------------------------------------------------------------


class CanvasTabResponse(BaseModel):
    id: str
    label: str
    widget_type: str
    data: dict[str, JsonValue]
    # Durable content handle for artifact-backed tabs (#1253).
    artifact_id: str | None = None


class CanvasStateResponse(BaseModel):
    schema_version: int
    active_tab: str | None
    next_tab_id: int
    tabs: list[CanvasTabResponse]


# Schema-only inputs: handlers retain legacy JSON parsing and status codes.
class CanvasNewTabRequest(BaseModel):
    widget_type: str
    data: dict[str, object]
    label: str | None = None
    background: bool = False


class CanvasTabRequest(BaseModel):
    tab_id: str


class CanvasNewTabResponse(BaseModel):
    ok: Literal[True]
    tab_id: str


class CanvasMutationResponse(BaseModel):
    ok: Literal[True]


def _user_owns_conv(config, conv_id: str, username: str) -> bool:
    """Authorization gate for canvas routes — caller must own the conv."""
    from .conversations import ConversationIndex

    index = ConversationIndex(config)
    conv = index.get(conv_id)
    return bool(conv and conv.user_id == username)


async def get_canvas_state(request: Request, conv_id: str) -> JSONResponse:
    """Load current canvas state for a conversation."""
    from .. import canvas as canvas_mod

    config = request.app.state.config
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    state = canvas_mod.read_canvas_state(config, conv_id)
    return JSONResponse(state)


class StickyResponse(BaseModel):
    widget_type: str | None
    data: dict[str, object] | None


async def get_sticky_state(request: Request, conv_id: str) -> StickyResponse | JSONResponse:
    """Load current sticky-slot state for a conversation (reload recovery)."""
    from .. import sticky as sticky_mod

    config = request.app.state.config
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    state = sticky_mod.read_sticky_state(config, conv_id)
    return StickyResponse(
        widget_type=state.get("widget_type"),
        data=state.get("data"),
    )


class StickySetRequest(BaseModel):
    widget_type: str
    data: dict[str, object]


async def post_sticky_state(request: Request, conv_id: str) -> JSONResponse:
    """Pin a widget into the conversation's sticky slot. Backs external
    progress updates (e.g. long-running scripts posting live status)."""
    from .. import sticky as sticky_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    widget_type = body.get("widget_type")
    data = body.get("data")
    if not isinstance(widget_type, str) or not widget_type:
        return JSONResponse({"error": "widget_type must be a non-empty string"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"error": "data must be a JSON object"}, status_code=400)
    emit = manager.emit if manager else None
    result = await sticky_mod.set_sticky(config, conv_id, widget_type, data, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


async def delete_sticky_state(request: Request, conv_id: str) -> JSONResponse:
    """Clear the conversation's sticky slot."""
    from .. import sticky as sticky_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    emit = manager.emit if manager else None
    result = await sticky_mod.clear_sticky(config, conv_id, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=500)
    return JSONResponse({"ok": True})


async def post_canvas_new_tab(request: Request, conv_id: str) -> JSONResponse:
    """Create a new canvas tab. Backs the inline 'Open in Canvas' button."""
    from .. import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    widget_type = body.get("widget_type", "")
    data = body.get("data") or {}
    label = body.get("label")
    background = bool(body.get("background", False))
    emit = manager.emit if manager else None
    result = await canvas_mod.new_tab(
        config,
        conv_id,
        widget_type,
        data,
        label=label,
        emit=emit,
        enforce_agent_createable=False,
        background=background,
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True, "tab_id": result.tab_id})


async def post_canvas_active_tab(request: Request, conv_id: str) -> JSONResponse:
    """Set the active tab via user click in the panel."""
    from .. import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tab_id = body.get("tab_id", "")
    emit = manager.emit if manager else None
    result = await canvas_mod.set_active_tab(config, conv_id, tab_id, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


async def post_canvas_close_tab(request: Request, conv_id: str) -> JSONResponse:
    """Close a tab via user [×] click."""
    from .. import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tab_id = body.get("tab_id", "")
    emit = manager.emit if manager else None
    result = await canvas_mod.close_tab(
        config, conv_id, tab_id, emit=emit, registry=request.app.state.terminal_registry
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


@_authenticated
async def get_canvas_page(request: Request, username: str):
    """Serve the standalone canvas HTML (bare or tab-locked URL)."""
    config = request.app.state.config
    conv_id = request.path_params.get("conv_id", "")
    tab_id = request.path_params.get("tab_id", "")  # may be empty for bare URL
    if not _is_safe_conv_id(conv_id):
        return Response("Invalid conversation id", status_code=400)
    if tab_id and not _is_safe_conv_id(tab_id):
        return Response("Invalid tab id", status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return Response("Not found", status_code=404)
    html_path = Path(__file__).parent / "static" / "canvas-page.html"
    return Response(html_path.read_text(), media_type="text/html")


# -- Router --------------------------------------------------------------------

widget_router = APIRouter()

widget_router.add_api_route("/api/widgets", list_widgets, methods=["GET"], response_model=WidgetCatalogResponse)
widget_router.add_api_route("/widgets/{tier}/{name}/widget.js", serve_widget_js, methods=["GET"])
widget_router.add_api_route(
    "/api/canvas/{conv_id}", get_canvas_state, methods=["GET"], response_model=CanvasStateResponse
)
widget_router.add_api_route("/api/sticky/{conv_id}", get_sticky_state, methods=["GET"], response_model=StickyResponse)
widget_router.add_api_route(
    "/api/sticky/{conv_id}",
    post_sticky_state,
    methods=["POST"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": StickySetRequest.model_json_schema()}},
        }
    },
)
widget_router.add_api_route("/api/sticky/{conv_id}", delete_sticky_state, methods=["DELETE"])
widget_router.add_api_route(
    "/api/canvas/{conv_id}/new_tab",
    post_canvas_new_tab,
    methods=["POST"],
    response_model=CanvasNewTabResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CanvasNewTabRequest.model_json_schema()}},
        }
    },
)
widget_router.add_api_route(
    "/api/canvas/{conv_id}/active_tab",
    post_canvas_active_tab,
    methods=["POST"],
    response_model=CanvasMutationResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CanvasTabRequest.model_json_schema()}},
        }
    },
)
widget_router.add_api_route(
    "/api/canvas/{conv_id}/close_tab",
    post_canvas_close_tab,
    methods=["POST"],
    response_model=CanvasMutationResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CanvasTabRequest.model_json_schema()}},
        }
    },
)
widget_router.add_api_route("/canvas/{conv_id}", get_canvas_page, methods=["GET"])
widget_router.add_api_route("/canvas/{conv_id}/{tab_id}", get_canvas_page, methods=["GET"])
