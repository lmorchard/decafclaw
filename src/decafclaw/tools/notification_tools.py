"""``send_notification`` agent tool — emit an inbox notification.

Parallel to what internal producers (``heartbeat``, ``schedules``,
background-job exit) already do, but exposed as an agent tool so the
agent can announce events it considers noteworthy from within a
conversation. Also useful for manually smoke-testing notification
channels.

Writes to the inbox (durable) and publishes the ``notification_created``
event on the bus, which fan-outs to subscribed channels (Mattermost DM,
email, vault page, ...). ``conv_id`` is auto-populated from the agent's
current context.
"""

from __future__ import annotations

import logging

from decafclaw.media import ToolResult
from decafclaw.notifications import notify

log = logging.getLogger(__name__)


_VALID_PRIORITIES = {"low", "normal", "high"}


async def tool_send_notification(
    ctx, title: str, body: str = "",
    category: str = "agent", priority: str = "normal",
    link: str | None = None,
) -> ToolResult:
    """Emit a notification to the inbox; fan out to configured channels."""
    log.info(
        "[tool:send_notification] category=%s priority=%s title=%r",
        category, priority, title,
    )

    if not title or not title.strip():
        return ToolResult(text="[error: title is required]")
    if priority not in _VALID_PRIORITIES:
        return ToolResult(
            text=(f"[error: invalid priority {priority!r} — must be "
                  f"one of {sorted(_VALID_PRIORITIES)}]")
        )

    record = await notify(
        ctx.config, ctx.event_bus,
        category=category, title=title, body=body,
        priority=priority, link=link,
        conv_id=ctx.conv_id or None,
    )
    return ToolResult(
        text=f"Notification sent (id={record.id}, category={category}, "
             f"priority={priority}).",
    )


NOTIFICATION_TOOLS = {"send_notification": tool_send_notification}

NOTIFICATION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "send_notification",
            "description": (
                "Emit a notification to the agent's local inbox and fan "
                "it out to any configured delivery channels (Mattermost "
                "DM, email, vault page daily log, etc.). Use when you "
                "want to surface something outside the current "
                "conversation — a periodic status, an alert, a heads-up "
                "for async work the user is waiting on — or to smoke-test "
                "a channel's configuration. Inbox write is durable even "
                "if channel delivery fails. The agent's current "
                "conv_id is attached automatically so recipients can "
                "link back."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Short, scannable headline for the notification. "
                            "Shows up as the subject / first line in every "
                            "channel."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "Optional longer description. Kept under ~200 "
                            "characters for readability in dropdowns / DMs."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Free-form label used for grouping and channel "
                            "filters. Agent-initiated notifications default "
                            "to 'agent'. Reserved categories produced by "
                            "internal systems: 'heartbeat', 'schedule', "
                            "'background'. Pick something else for your own."
                        ),
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "normal", "high"],
                        "description": (
                            "Severity. Most channels filter by a minimum "
                            "priority (e.g. Mattermost DM defaults to 'high'); "
                            "use 'high' only for things that should interrupt."
                        ),
                    },
                    "link": {
                        "type": "string",
                        "description": (
                            "Optional URL or scheme-prefixed link attached to "
                            "the record. `conv://<id>` and `vault://<path>` "
                            "are recognized by the web UI; `http(s)://` links "
                            "work everywhere."
                        ),
                    },
                },
                "required": ["title"],
            },
        },
    },
]
