"""NTFY push channel adapter (#1494).

Subscribes to two event types:

- ``notification_created`` — publishes inbox records at or above the
  channel's ``min_priority`` to the configured ntfy topic (default
  priority 1, min/lowest urgency).
- ``confirmation_request`` — when ``notify_on_confirmation`` is set,
  publishes a pending tool confirmation as a medium-urgency notification
  (default priority 3, default) with
  ``Approve`` / ``Deny`` action buttons (``action=http``) and an
  ``Open Session`` link (``action=view``).

The action buttons POST a single-use token from the shared
``ConfirmTokenRegistry`` to ``/actions/confirm``; the HTTP handler
consumes the token and resolves the confirmation through the
``ConversationManager``. Buttons are only included when
``config.http.enabled`` — without the callback server they would dead-end.

Delivery is fire-and-forget via ``asyncio.create_task``; failures log at
warning level and are swallowed. The inbox record remains the source of
truth. See ``docs/notifications.md``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import httpx

from decafclaw.mattermost_ui import get_token_registry
from decafclaw.notifications import NotificationRecord

from . import meets_priority

log = logging.getLogger(__name__)

# ntfy's priority scale: 1=min 2=low 3=default 4=high 5=urgent.
DEFAULT_NOTIFICATION_PRIORITY = 1
CONFIRMATION_PRIORITY = 3
_PRIORITY: dict[str, int] = {}
REQUEST_TIMEOUT_SEC = 10.0


def _publish_url(cfg: Any) -> str:
    """Return the ntfy publish endpoint, or "" for an unusable config.

    ``server_url`` must be an absolute http(s) URL and ``topic`` must be
    non-empty; anything else disables delivery instead of guessing.

    When publishing as JSON, ntfy requires POSTing to the root URL (not
    ``{server}/{topic}``); the topic is specified inside the payload body.
    POSTing to the topic URL causes ntfy to treat the body as raw text.
    """
    server = (cfg.server_url or "").strip().rstrip("/")
    topic = (cfg.topic or "").strip()
    if not server.startswith(("http://", "https://")):
        return ""
    if not topic:
        return ""
    return f"{server}/"


def _headers(cfg: Any) -> dict[str, str]:
    """Build request headers, including the bearer token when configured."""
    headers = {"Content-Type": "application/json"}
    if cfg.access_token:
        headers["Authorization"] = f"Bearer {cfg.access_token}"
    return headers


def _web_base(config: Any) -> str:
    """Web UI base URL reachable from a phone, or "" when HTTP is disabled."""
    if not config.http.enabled:
        return ""
    return (config.http.base_url or config.http_callback_base).rstrip("/")


def _session_url(config: Any, conv_id: str) -> str:
    base = _web_base(config)
    if not base or not conv_id:
        return ""
    return f"{base}/?conv={conv_id}"


def _click_url(config: Any, record: NotificationRecord) -> str:
    if record.link and record.link.startswith(("http://", "https://")):
        return record.link
    return _session_url(config, record.conv_id or "")


def build_notification_payload(config: Any, record: NotificationRecord) -> dict:
    """Render a notification record as an ntfy JSON publish payload."""
    cfg = config.notifications.channels.ntfy
    default_priority = getattr(cfg, "default_priority", DEFAULT_NOTIFICATION_PRIORITY)
    priority = _PRIORITY.get(record.priority, default_priority)
    payload: dict[str, Any] = {
        "topic": cfg.topic.strip(),
        "title": record.title,
        "message": record.body or record.title or "Notification",
        "priority": priority,
    }
    if record.category:
        payload["tags"] = [record.category]
    click = _click_url(config, record)
    if click:
        payload["click"] = click
    return payload


def _confirmation_title(config: Any, event: dict) -> str:
    action_type = (event.get("action_type") or "confirmation").replace("_", " ")
    label = action_type[:1].upper() + action_type[1:]
    agent_id = config.agent.id or ""
    prefix = f"[{agent_id}] " if agent_id else ""
    return f"{prefix}Approval needed: {label}"


def _confirmation_detail(event: dict) -> str:
    message = (event.get("message") or "").strip()
    if message:
        return message
    action_data = event.get("action_data") or {}
    detail = action_data.get("command") or action_data.get("skill_name") or ""
    return detail or "The agent is waiting for your decision."


def _mint_token(config: Any, event: dict, action: str) -> str:
    """Mint a single-use callback token bound to the pending confirmation."""
    return get_token_registry().create(
        context_id=event.get("conv_id", ""),
        tool_name=event.get("action_type", ""),
        original_message=(event.get("message") or "")[:2000],
        server_secret=config.http.secret,
        action=action,
        conv_id=event.get("conv_id", ""),
        confirmation_id=event.get("confirmation_id", ""),
    )


def build_confirmation_payload(config: Any, event: dict) -> dict:
    """Render a ``confirmation_request`` event as an ntfy push payload.

    Actions are only minted when the HTTP callback server is enabled and
    the event carries both routing ids; otherwise the push degrades to a
    plain (or view-only) notification.
    """
    cfg = config.notifications.channels.ntfy
    conv_id = event.get("conv_id", "")
    conf_priority = getattr(cfg, "confirmation_priority", CONFIRMATION_PRIORITY)
    payload: dict[str, Any] = {
        "topic": cfg.topic.strip(),
        "title": _confirmation_title(config, event),
        "message": _confirmation_detail(event),
        "priority": conf_priority,
        "tags": ["warning"],
    }

    actions: list[dict] = []
    if config.http.enabled and conv_id and event.get("confirmation_id"):
        confirm_url = f"{config.http_callback_base}/actions/confirm"
        for action, label in (("approve", "Approve"), ("deny", "Deny")):
            token = _mint_token(config, event, action)
            actions.append(
                {
                    "action": "http",
                    "label": label,
                    "url": f"{confirm_url}?token={token}",
                    "method": "POST",
                    "clear": True,
                }
            )

    session = _session_url(config, conv_id)
    if session:
        actions.append({"action": "view", "label": "Open Session", "url": session})
        payload["click"] = session
    if actions:
        payload["actions"] = actions
    return payload


async def _deliver(cfg: Any, payload: dict) -> None:
    """POST one payload to ntfy. Failures log and are otherwise swallowed."""
    url = _publish_url(cfg)
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
            response = await client.post(url, json=payload, headers=_headers(cfg))
            response.raise_for_status()
    except Exception as exc:
        log.warning(
            "NTFY delivery failed (topic=%s title=%s): %s",
            cfg.topic,
            payload.get("title", ""),
            exc,
        )


def make_ntfy_adapter(config: Any) -> Callable[[dict], Awaitable[None]]:
    """Return an event-bus handler that pushes notifications to ntfy.

    Config is resolved per event so in-process mutations take effect on
    the next event. ``confirmation_request`` events are published on the
    global bus by ``ConversationManager`` with ``conv_id`` attached.
    """

    async def handle(event: dict) -> None:
        cfg = config.notifications.channels.ntfy
        if not cfg.enabled or not _publish_url(cfg):
            return

        event_type = event.get("type")
        if event_type == "notification_created":
            record = NotificationRecord.from_dict(event.get("record", {}))
            if not meets_priority(record.priority, cfg.min_priority):
                return
            payload = build_notification_payload(config, record)
        elif event_type == "confirmation_request":
            # Widget responses need structured data from the web UI;
            # Approve/Deny buttons cannot supply it (Mattermost skips
            # them for the same reason).
            if event.get("action_type") == "widget_response":
                return
            if not cfg.notify_on_confirmation:
                return
            payload = build_confirmation_payload(config, event)
        else:
            return

        # Fire-and-forget — never block the publishing turn on ntfy I/O.
        asyncio.create_task(_deliver(cfg, payload))

    return handle
