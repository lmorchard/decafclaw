"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)


def create_app(config, event_bus) -> Starlette:
    """Create the Starlette ASGI app with routes."""

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_confirm(request: Request) -> JSONResponse:
        """Handle Mattermost interactive button callbacks for tool confirmation."""
        # Verify shared secret
        secret = request.query_params.get("secret", "")
        if secret != config.http_secret:
            log.warning("Confirm callback rejected: invalid secret")
            return JSONResponse({"error": "invalid secret"}, status_code=403)

        body = await request.json()
        context = body.get("context", {})
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

    callback_url = f"{config.http_callback_base}/actions/confirm?secret={config.http_secret}"

    # Base context included in every button
    base_context = {
        "context_id": context_id,
        "tool": tool_name,
        "original_message": original_message[:2000],  # truncate for Mattermost limits
    }

    if tool_name == "shell" and suggested_pattern:
        # Shell tool: Approve / Deny / Allow Pattern (no Always)
        actions = [
            {
                "id": "approve",
                "name": "Approve",
                "style": "primary",
                "integration": {
                    "url": callback_url,
                    "context": {**base_context, "action": "approve"},
                },
            },
            {
                "id": "deny",
                "name": "Deny",
                "style": "danger",
                "integration": {
                    "url": callback_url,
                    "context": {**base_context, "action": "deny"},
                },
            },
            {
                "id": "add_pattern",
                "name": f"Allow: {suggested_pattern}",
                "integration": {
                    "url": callback_url,
                    "context": {
                        **base_context,
                        "action": "add_pattern",
                        "suggested_pattern": suggested_pattern,
                    },
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
                    "url": callback_url,
                    "context": {**base_context, "action": "approve"},
                },
            },
            {
                "id": "deny",
                "name": "Deny",
                "style": "danger",
                "integration": {
                    "url": callback_url,
                    "context": {**base_context, "action": "deny"},
                },
            },
            {
                "id": "always",
                "name": "Always",
                "integration": {
                    "url": callback_url,
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
