"""Terminal resource routes — per-resource route module.

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

The terminal resource serves the canvas terminal tab: a dedicated WebSocket
route, ``/ws/terminal/{conv_id}/{tab_id}``, that attaches a browser connection
to a PTY session (see docs/web-terminal.md). The gateway itself lives in
``web/websocket.py``; this module only hosts the route's thin entry point.
"""

from fastapi import APIRouter
from starlette.websockets import WebSocket


async def ws_terminal(websocket: WebSocket):
    """WebSocket entry point — defers to the gateway in `web/websocket.py`."""
    from .websocket import websocket_terminal

    state = websocket.app.state
    await websocket_terminal(websocket, state.config, state.terminal_registry)


# -- Router --------------------------------------------------------------------

terminal_router = APIRouter()
terminal_router.add_api_websocket_route("/ws/terminal/{conv_id}/{tab_id}", ws_terminal)
