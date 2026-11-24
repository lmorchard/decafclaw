"""Shared HTTP auth helpers for web route modules.

The ``_authenticated`` decorator and the username lookup are cross-cutting:
every per-resource route module (see ``workspace_routes.py``) applies them, and
so does the still-monolithic ``http_server``. They live in this leaf module —
which depends only on :mod:`decafclaw.web.auth` and Starlette/FastAPI — so both
``http_server`` and the resource modules can import them at module level without
a circular import.

Handlers read their dependencies off ``request.app.state`` (populated by
``create_app``); the decorator only resolves the authenticated username.
"""

from fastapi import Request
from starlette.responses import JSONResponse


def _get_username_or_401(request: Request) -> str | None:
    """Resolve the authenticated username from cookies, or None.

    Used by handlers that need the username outside the
    ``_authenticated`` decorator (currently the WebSocket entry point,
    which has its own signature). Returning ``None`` means "no valid
    session"; callers decide whether to 401 or return a redirect.
    """
    from .auth import get_current_user

    return get_current_user(request, request.app.state.config)


def _authenticated(handler):
    """Decorator: extracts the authenticated username, 401 if missing.

    Forwards to ``handler(request, username)``. Reads the active
    ``Config`` off ``request.app.state``, set up by ``create_app``.
    """

    async def wrapper(request: Request):
        username = _get_username_or_401(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        return await handler(request, username)

    return wrapper
