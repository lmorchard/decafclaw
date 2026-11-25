"""Notifications resource routes — per-resource route module.

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

The notifications resource serves the web UI's inbox: list, unread count, and
mark-read endpoints under ``/api/notifications``.

Phase 1: single-user. Inbox + read-state live in the agent workspace,
not partitioned by authenticated user. All authenticated callers see
the same records. Multi-user partitioning is tracked in docs/notifications.md
"Coming in Phase 2+". Do not expose across tenants until partitioning lands.
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from .. import notifications as notifs
from .http_auth import _get_username_or_401

log = logging.getLogger(__name__)


# -- Models --------------------------------------------------------------------


class NotificationResponse(BaseModel):
    id: str
    timestamp: str
    category: str
    title: str
    priority: str
    body: str
    link: str | None
    conv_id: str | None
    read: bool


class NotificationListResponse(BaseModel):
    records: list[NotificationResponse]
    has_more: bool


class NotificationCountResponse(BaseModel):
    count: int


class NotificationReadResponse(BaseModel):
    ok: bool


# -- Handlers ------------------------------------------------------------------


async def list_notifications(request: Request) -> JSONResponse:
    """Return inbox records newest first, with a joined ``read`` bool."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    config = request.app.state.config
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    if limit <= 0 or limit > 200:
        return JSONResponse({"error": "limit must be in [1, 200]"}, status_code=400)
    before = request.query_params.get("before") or None
    records, has_more = notifs.read_inbox(config, limit=limit, before=before)
    read_ids = notifs.get_read_ids(config)
    return JSONResponse(
        {
            "records": [{**r.to_dict(), "read": r.id in read_ids} for r in records],
            "has_more": has_more,
        }
    )


async def notifications_unread_count(request: Request) -> JSONResponse:
    """Return ``{"count": N}`` — called frequently, stays cheap."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    return JSONResponse({"count": notifs.unread_count(request.app.state.config)})


async def notifications_mark_read(request: Request, id: str) -> JSONResponse:
    """Mark a single notification read. Idempotent."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    config = request.app.state.config
    event_bus = request.app.state.event_bus
    record_id = id
    if not record_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    await notifs.mark_read(config, record_id, event_bus=event_bus)
    return JSONResponse({"ok": True})


async def notifications_mark_all_read(request: Request) -> JSONResponse:
    """Mark all currently-visible notifications read."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    await notifs.mark_all_read(
        request.app.state.config,
        event_bus=request.app.state.event_bus,
    )
    return JSONResponse({"ok": True})


# -- Router --------------------------------------------------------------------

notification_router = APIRouter()

notification_router.add_api_route(
    "/api/notifications",
    list_notifications,
    methods=["GET"],
    response_model=NotificationListResponse,
    # Schema-only queries preserve legacy parsing and 400 responses.
    openapi_extra={
        "parameters": [
            {
                "name": "limit",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
            {"name": "before", "in": "query", "required": False, "schema": {"type": "string"}},
        ]
    },
)
notification_router.add_api_route(
    "/api/notifications/unread-count",
    notifications_unread_count,
    methods=["GET"],
    response_model=NotificationCountResponse,
)
notification_router.add_api_route(
    "/api/notifications/read-all",
    notifications_mark_all_read,
    methods=["POST"],
    response_model=NotificationReadResponse,
)
notification_router.add_api_route(
    "/api/notifications/{id}/read",
    notifications_mark_read,
    methods=["POST"],
    response_model=NotificationReadResponse,
)
