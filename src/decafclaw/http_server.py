"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import hashlib
import logging
import secrets

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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


def create_app(config, event_bus) -> Starlette:
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
        else:
            action = context.get("action", "")
            context_id = context.get("context_id", "")
            tool_name = context.get("tool", "")
            original_message = context.get("original_message", "")

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

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/actions/confirm", handle_confirm, methods=["POST"]),
    ]

    return Starlette(routes=routes)


def build_confirm_buttons(config, tool_name: str, command: str,
                          suggested_pattern: str, context_id: str,
                          original_message: str) -> list[dict]:
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
        )

    base_url = f"{config.http_callback_base}/actions/confirm"

    # Base context included in every button
    base_context = {
        "context_id": context_id,
        "tool": tool_name,
    }

    if tool_name == "shell" and suggested_pattern:
        # Shell tool: Approve / Deny / Allow Pattern (no Always)
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
                "id": "add_pattern",
                "name": f"Allow: {suggested_pattern}",
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


async def run_http_server(config, event_bus) -> None:
    """Start the HTTP server as an asyncio task."""
    import uvicorn
    app = create_app(config, event_bus)
    server_config = uvicorn.Config(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level="info",
    )
    server = uvicorn.Server(server_config)
    log.info(f"HTTP server starting on {config.http_host}:{config.http_port}")
    await server.serve()
