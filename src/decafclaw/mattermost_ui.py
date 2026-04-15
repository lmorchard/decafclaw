"""Mattermost UI helpers — confirmation buttons, stop buttons, token registry.

These are Mattermost-specific attachment builders used by both mattermost.py
(for rendering) and http_server.py (for token validation). Extracted from
http_server.py to avoid the HTTP server being a dependency of the Mattermost client.
"""

import hashlib
import logging
import secrets

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
        """Generate a token for a pending confirmation. Returns the token."""
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


# Module-level registry shared between mattermost.py and http_server.py
_token_registry = ConfirmTokenRegistry()


def get_token_registry() -> ConfirmTokenRegistry:
    """Get the global token registry."""
    return _token_registry


def build_confirm_buttons(config, tool_name: str, command: str,
                          suggested_pattern: str, context_id: str,
                          original_message: str, tool_call_id: str = "",
                          conv_id: str = "",
                          confirmation_id: str = "") -> list[dict]:
    """Build Mattermost attachment with interactive action buttons.

    Returns the attachments list to include in a post's props.
    Returns [] if HTTP server is not enabled.
    """
    if not config.http.enabled:
        return []

    def _make_token(action: str) -> str:
        return _token_registry.create(
            context_id=context_id,
            tool_name=tool_name,
            original_message=original_message[:2000],
            server_secret=config.http.secret,
            action=action,
            tool_call_id=tool_call_id,
            conv_id=conv_id,
            confirmation_id=confirmation_id,
        )

    base_url = f"{config.http_callback_base}/actions/confirm"

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
    if not config.http.enabled:
        return []

    token = _token_registry.create(
        context_id=conv_id,
        tool_name="_cancel",
        original_message="",
        server_secret=config.http.secret,
        conv_id=conv_id,
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
