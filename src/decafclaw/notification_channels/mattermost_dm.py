"""Mattermost direct-message channel adapter for notifications.

Subscribes to the ``notification_created`` event bus event. When a record
clears the configured ``min_priority`` threshold, formats a short markdown
DM and hands off delivery to ``MattermostClient.post_direct_message`` via
an ``asyncio.create_task`` so the publishing ``notify()`` call doesn't
wait on network I/O.

Wired up at startup from ``runner.py``; delivery failures log at warning
level and are otherwise swallowed — the inbox is the source of truth.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from decafclaw.notifications import NotificationRecord

from . import PRIORITY_GLYPH, meets_priority

log = logging.getLogger(__name__)


def _format_dm(record: NotificationRecord, base_url: str) -> str:
    """Render a notification as a short markdown DM body.

    Priority glyph + title on the header line, body (if any) on the next
    lines, and an optional link if ``base_url`` is configured and the
    record carries a ``conv_id`` or explicit ``link``.
    """
    glyph = PRIORITY_GLYPH.get(record.priority, "🔔")
    parts = [f"{glyph} **{record.title}**"]
    if record.body:
        parts.append(record.body)
    link = _resolve_link(record, base_url)
    if link:
        parts.append(f"→ <{link}>")
    return "\n".join(parts)


def _resolve_link(record: NotificationRecord, base_url: str) -> str | None:
    """Pick the link URL for a DM, or None."""
    if record.link and (record.link.startswith("http://")
                        or record.link.startswith("https://")):
        return record.link
    if base_url and record.conv_id:
        return f"{base_url.rstrip('/')}/#conv={record.conv_id}"
    return None


def make_mattermost_dm_adapter(
    config: Any, mm_client: Any,
) -> Callable[[dict], Awaitable[None]]:
    """Return an event-bus handler that DMs notifications to a Mattermost user.

    The returned coroutine is what ``event_bus.subscribe(...)`` expects.
    Closes over ``config`` and ``mm_client`` so the handler has everything
    it needs without a lookup at event-fire time. All config fields
    (recipient, min_priority, base_url) are resolved **per event** from
    the live ``config`` object so in-process config mutations take effect
    on the next notification — config-file edits still require a
    restart, since there's no file-reload mechanism today.
    """

    async def _deliver(record: NotificationRecord, recipient: str,
                       base_url: str) -> None:
        """Background-task delivery — fire-and-forget from the handler."""
        try:
            body = _format_dm(record, base_url)
            result = await mm_client.post_direct_message(recipient, body)
            if result is None:
                log.warning(
                    "Mattermost DM delivery failed: recipient '%s' not found "
                    "(category=%s priority=%s conv=%s)",
                    recipient, record.category, record.priority,
                    record.conv_id or "-",
                )
        except Exception as exc:
            log.warning(
                "Mattermost DM delivery failed (recipient=%s category=%s "
                "priority=%s conv=%s): %s",
                recipient, record.category, record.priority,
                record.conv_id or "-", exc,
            )

    async def handle(event: dict) -> None:
        if event.get("type") != "notification_created":
            return
        # Resolve all config per event so delivery and filtering see the
        # same values — avoids split-brain if `recipient_username` is
        # mutated in-process between construction and dispatch.
        cfg = config.notifications.channels.mattermost_dm
        recipient = cfg.recipient_username
        if not cfg.enabled or not recipient:
            return
        record = NotificationRecord.from_dict(event["record"])
        if not meets_priority(record.priority, cfg.min_priority):
            return
        base_url = config.http.base_url
        # Fire-and-forget — don't make notify() wait on Mattermost I/O.
        asyncio.create_task(_deliver(record, recipient, base_url))

    return handle
