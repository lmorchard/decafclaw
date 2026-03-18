"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import hashlib
import logging
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

log = logging.getLogger(__name__)


class ConfirmTokenRegistry:
    """Single-use token registry for confirmation callbacks.

    Each pending confirmation gets a unique token. The token maps to the
    confirmation metadata (context_id, tool, original_message). Tokens are
    consumed on use — a captured URL cannot be replayed.
    """

    def __init__(self):
        self._tokens: dict[str, dict] = {}

    def create(self, context_id: str, tool_name: str,
               original_message: str, server_secret: str = "", **extra) -> str:
        """Generate a token for a pending confirmation. Returns the token.

        If server_secret is provided, the token is an HMAC of a random
        nonce — making it both unguessable and tied to the server secret.
        """
        nonce = secrets.token_urlsafe(24)
        if server_secret:
            token = hashlib.sha256(f"{server_secret}:{nonce}".encode()).hexdigest()[:32]
        else:
            token = nonce
        self._tokens[token] = {
            "context_id": context_id,
            "tool": tool_name,
            "original_message": original_message,
            **extra,
        }
        return token

    def consume(self, token: str) -> dict | None:
        """Look up and remove a token. Returns the metadata or None."""
        return self._tokens.pop(token, None)

    def __len__(self) -> int:
        return len(self._tokens)


# Module-level registry shared between create_app and build_confirm_buttons
_token_registry = ConfirmTokenRegistry()


def get_token_registry() -> ConfirmTokenRegistry:
    """Get the global token registry."""
    return _token_registry


def create_app(config, event_bus, app_ctx=None) -> Starlette:
    """Create the Starlette ASGI app with routes."""

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_confirm(request: Request) -> JSONResponse:
        """Handle Mattermost interactive button callbacks for tool confirmation."""
        # Verify token (single-use, per-confirmation)
        token = request.query_params.get("token", "")
        token_data = _token_registry.consume(token)

        # Also check static secret as fallback (defense in depth)
        secret = request.query_params.get("secret", "")
        has_valid_secret = config.http_secret and secret == config.http_secret

        if not token_data and not has_valid_secret:
            log.warning("Confirm callback rejected: invalid token and no valid secret")
            return JSONResponse({"error": "unauthorized"}, status_code=403)

        body = await request.json()
        context = body.get("context", {})

        # Use token data if available, fall back to POST body context
        if token_data:
            action = token_data.get("action", "") or context.get("action", "")
            context_id = token_data["context_id"]
            tool_name = token_data["tool"]
            original_message = token_data["original_message"]
            tool_call_id = token_data.get("tool_call_id", "")
        else:
            action = context.get("action", "")
            context_id = context.get("context_id", "")
            tool_name = context.get("tool", "")
            original_message = context.get("original_message", "")
            tool_call_id = context.get("tool_call_id", "")

        log.info(f"Confirm callback: action={action} tool={tool_name} context={context_id[:8]}")

        # Map action to event fields
        approved = action in ("approve", "always", "add_pattern")
        always = action == "always"
        add_pattern = action == "add_pattern"

        # Publish confirmation event on the event bus
        await event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": context_id,
            "tool": tool_name,
            "approved": approved,
            **({"tool_call_id": tool_call_id} if tool_call_id else {}),
            **({"always": True} if always else {}),
            **({"add_pattern": True} if add_pattern else {}),
        })

        # Determine result label
        labels = {
            "approve": "\u2705 Approved",
            "always": "\u2705 Always approved",
            "add_pattern": "\U0001f4d3 Approved + pattern added",
            "deny": "\U0001f44e Denied",
        }
        label = labels.get(action, f"\u2753 Unknown action: {action}")

        # Return update response — removes buttons, shows result
        return JSONResponse({
            "update": {
                "message": f"{original_message}\n\n**Result:** {label}",
                "props": {"attachments": []},
            }
        })

    # -- Auth routes -----------------------------------------------------------

    async def auth_login(request: Request) -> JSONResponse:
        """Validate token, set session cookie."""
        from .web.auth import validate_token
        body = await request.json()
        token = body.get("token", "")
        username = validate_token(config, token)
        if not username:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        response = JSONResponse({"username": username})
        response.set_cookie(
            "decafclaw_session", token,
            httponly=True, samesite="lax", max_age=30 * 24 * 3600,
        )
        return response

    async def auth_logout(request: Request) -> JSONResponse:
        """Clear session cookie."""
        response = JSONResponse({"ok": True})
        response.delete_cookie("decafclaw_session")
        return response

    async def auth_me(request: Request) -> JSONResponse:
        """Return current authenticated user."""
        from .web.auth import get_current_user
        username = get_current_user(request, config)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        return JSONResponse({"username": username})

    # -- Conversation routes ---------------------------------------------------

    def _require_auth(request):
        """Helper: get username from cookie or None."""
        from .web.auth import get_current_user
        return get_current_user(request, config)

    async def list_conversations(request: Request) -> JSONResponse:
        """List conversations for the authenticated user."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        convs = index.list_for_user(username)
        return JSONResponse([c.to_dict() for c in convs])

    async def create_conversation(request: Request) -> JSONResponse:
        """Create a new conversation."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        body = await request.json()
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.create(username, title=body.get("title", ""))
        return JSONResponse(conv.to_dict(), status_code=201)

    async def get_conversation(request: Request) -> JSONResponse:
        """Get conversation metadata."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(conv.to_dict())

    async def rename_conversation(request: Request) -> JSONResponse:
        """Rename a conversation."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        conv_id = request.path_params["id"]
        body = await request.json()
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        updated = index.rename(conv_id, body.get("title", ""))
        if not updated:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(updated.to_dict())

    async def get_conversation_history(request: Request) -> JSONResponse:
        """Load paginated conversation history."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        limit = int(request.query_params.get("limit", "50"))
        before = request.query_params.get("before", "")
        messages, has_more = index.load_history(conv_id, limit=limit, before=before)
        return JSONResponse({"messages": messages, "has_more": has_more})

    async def serve_workspace_file(request: Request):
        """Serve a file from the agent workspace (authenticated, read-only)."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        file_path = request.path_params.get("path", "")
        if not file_path:
            return JSONResponse({"error": "path required"}, status_code=400)
        # Resolve and sandbox to workspace
        import mimetypes
        workspace = config.workspace_path.resolve()
        resolved = (workspace / file_path).resolve()
        if not str(resolved).startswith(str(workspace)):
            return JSONResponse({"error": "path outside workspace"}, status_code=403)
        if not resolved.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        return FileResponse(str(resolved), media_type=content_type)

    async def archive_conversation(request: Request) -> JSONResponse:
        """Archive a conversation (hide from list, keep data)."""
        username = _require_auth(request)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        index.archive(conv_id)
        return JSONResponse({"ok": True})

    # -- WebSocket route -------------------------------------------------------

    async def ws_chat(websocket):
        from .web.websocket import websocket_chat
        await websocket_chat(websocket, config, event_bus, app_ctx)

    async def handle_cancel(request: Request) -> JSONResponse:
        """Handle Mattermost interactive button callback for stop/cancel."""
        token = request.query_params.get("token", "")
        token_data = _token_registry.consume(token)

        if not token_data:
            log.warning("Cancel callback rejected: invalid or expired token")
            return JSONResponse({"error": "unauthorized"}, status_code=403)

        conv_id = token_data["context_id"]
        log.info(f"Cancel button pressed for conversation {conv_id[:8]}")

        await event_bus.publish({
            "type": "cancel_turn",
            "conv_id": conv_id,
        })

        return JSONResponse({
            "update": {
                "message": "\u23f9\ufe0f Stopped",
                "props": {"attachments": []},
            }
        })

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/actions/confirm", handle_confirm, methods=["POST"]),
        Route("/actions/cancel", handle_cancel, methods=["POST"]),
        Route("/api/auth/login", auth_login, methods=["POST"]),
        Route("/api/auth/logout", auth_logout, methods=["POST"]),
        Route("/api/auth/me", auth_me, methods=["GET"]),
        Route("/api/conversations", list_conversations, methods=["GET"]),
        Route("/api/conversations", create_conversation, methods=["POST"]),
        Route("/api/conversations/{id}", get_conversation, methods=["GET"]),
        Route("/api/conversations/{id}", rename_conversation, methods=["PATCH"]),
        Route("/api/conversations/{id}/history", get_conversation_history, methods=["GET"]),
        Route("/api/conversations/{id}/archive", archive_conversation, methods=["POST"]),
        Route("/api/workspace/{path:path}", serve_workspace_file, methods=["GET"]),
        WebSocketRoute("/ws/chat", ws_chat),
    ]

    # Static file serving for web UI
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.is_dir():
        async def serve_index(request: Request):
            return FileResponse(static_dir / "index.html")

        routes.append(Route("/", serve_index, methods=["GET"]))
        routes.append(Mount("/static", StaticFiles(directory=str(static_dir)), name="static"))

    return Starlette(routes=routes)


def build_confirm_buttons(config, tool_name: str, command: str,
                          suggested_pattern: str, context_id: str,
                          original_message: str, tool_call_id: str = "") -> list[dict]:
    """Build Mattermost attachment with interactive action buttons.

    Returns the attachments list to include in a post's props.
    Returns [] if HTTP server is not enabled.
    """
    if not config.http_enabled:
        return []

    def _make_token(action: str) -> str:
        """Generate a per-button token."""
        return _token_registry.create(
            context_id=context_id,
            tool_name=tool_name,
            original_message=original_message[:2000],
            server_secret=config.http_secret,
            action=action,
            tool_call_id=tool_call_id,
        )

    base_url = f"{config.http_callback_base}/actions/confirm"

    # Base context included in every button
    base_context = {
        "context_id": context_id,
        "tool": tool_name,
        **({"tool_call_id": tool_call_id} if tool_call_id else {}),
    }

    if tool_name == "shell" and suggested_pattern:
        # Shell tool: Approve / Deny / Allow Pattern (no Always)
        # NOTE: button IDs must not contain underscores — Mattermost
        # silently drops callbacks for buttons with underscores in the ID.
        actions = [
            {
                "id": "approve",
                "name": "Approve",
                "style": "primary",
                "integration": {
                    "url": f"{base_url}?token={_make_token('approve')}",
                    "context": {**base_context, "action": "approve"},
                },
            },
            {
                "id": "deny",
                "name": "Deny",
                "style": "danger",
                "integration": {
                    "url": f"{base_url}?token={_make_token('deny')}",
                    "context": {**base_context, "action": "deny"},
                },
            },
            {
                "id": "allowpattern",
                "name": f"Allow Pattern: {suggested_pattern}",
                "style": "default",
                "integration": {
                    "url": f"{base_url}?token={_make_token('add_pattern')}",
                    "context": {**base_context, "action": "add_pattern"},
                },
            },
        ]
    else:
        # Other tools: Approve / Deny / Always
        actions = [
            {
                "id": "approve",
                "name": "Approve",
                "style": "primary",
                "integration": {
                    "url": f"{base_url}?token={_make_token('approve')}",
                    "context": {**base_context, "action": "approve"},
                },
            },
            {
                "id": "deny",
                "name": "Deny",
                "style": "danger",
                "integration": {
                    "url": f"{base_url}?token={_make_token('deny')}",
                    "context": {**base_context, "action": "deny"},
                },
            },
            {
                "id": "always",
                "name": "Always",
                "style": "default",
                "integration": {
                    "url": f"{base_url}?token={_make_token('always')}",
                    "context": {**base_context, "action": "always"},
                },
            },
        ]

    return [{
        "text": "",
        "actions": actions,
    }]


def build_stop_button(config, conv_id: str) -> list[dict]:
    """Build a Mattermost attachment with a Stop button for cancelling an agent turn.

    Returns [] if HTTP server is not enabled.
    """
    if not config.http_enabled:
        return []

    token = _token_registry.create(
        context_id=conv_id,
        tool_name="_cancel",
        original_message="",
        server_secret=config.http_secret,
    )
    base_url = f"{config.http_callback_base}/actions/cancel"

    return [{
        "text": "",
        "actions": [
            {
                "id": "stop",
                "name": "Stop",
                "style": "danger",
                "integration": {
                    "url": f"{base_url}?token={token}",
                    "context": {"conv_id": conv_id},
                },
            },
        ],
    }]


_http_server = None  # uvicorn.Server instance, set by run_http_server


async def run_http_server(config, event_bus, app_ctx=None) -> None:
    """Start the HTTP server as an asyncio task."""
    global _http_server
    import uvicorn
    app = create_app(config, event_bus, app_ctx=app_ctx)
    server_config = uvicorn.Config(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level="info",
    )
    _http_server = uvicorn.Server(server_config)
    log.info(f"HTTP server starting on {config.http_host}:{config.http_port}")
    await _http_server.serve()


async def shutdown_http_server() -> None:
    """Gracefully shut down the HTTP server (avoids CancelledError tracebacks)."""
    if _http_server is not None:
        _http_server.should_exit = True
