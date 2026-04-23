"""Email notification channel adapter.

Subscribes to the ``notification_created`` event bus event. When a
record clears the configured ``min_priority`` threshold, formats a
short plain-text email (priority glyph + title + body + optional link)
and hands off delivery to :func:`decafclaw.mail.send_mail` via an
``asyncio.create_task`` so the publishing ``notify()`` call doesn't
wait on SMTP I/O.

Wired up at startup from ``runner.py``; delivery failures log at
warning level and are otherwise swallowed — the inbox is the source of
truth. The channel's ``recipient_addresses`` list IS the trust
boundary; this adapter does NOT consult
``config.email.allowed_recipients`` (which applies only to the
``send_email`` tool).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from decafclaw.notifications import NotificationRecord

log = logging.getLogger(__name__)


_PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2}
_PRIORITY_GLYPH = {"low": "·", "normal": "🔔", "high": "⚠️"}


def _meets_priority(record_priority: str, min_priority: str) -> bool:
    return (_PRIORITY_ORDER.get(record_priority, 1)
            >= _PRIORITY_ORDER.get(min_priority, 1))


def _format_body(record: NotificationRecord, base_url: str) -> str:
    """Render a notification as a short plain-text email body.

    Same shape as the Mattermost DM body — priority glyph + title +
    body + optional link line — but without any markdown escaping
    since email clients render plain text literally.
    """
    glyph = _PRIORITY_GLYPH.get(record.priority, "🔔")
    parts = [f"{glyph} {record.title}"]
    if record.body:
        parts.append(record.body)
    link = _resolve_link(record, base_url)
    if link:
        parts.append(f"-> {link}")
    return "\n".join(parts)


def _resolve_link(record: NotificationRecord, base_url: str) -> str | None:
    if record.link and (record.link.startswith("http://")
                        or record.link.startswith("https://")):
        return record.link
    if base_url and record.conv_id:
        return f"{base_url.rstrip('/')}/#conv={record.conv_id}"
    return None


def _format_subject(agent_id: str, record: NotificationRecord) -> str:
    """Keep subjects short and scannable.

    Shape: ``[<agent_id>] [<category>] <title>``. The agent-id prefix
    lets the recipient identify which bot sent the message — useful when
    multiple DecafClaw instances share an inbox, or when the recipient
    is filtering with rules.
    """
    return f"[{agent_id}] [{record.category}] {record.title}"


def make_email_adapter(
    config: Any,
) -> Callable[[dict], Awaitable[None]]:
    """Return an event-bus handler that emails notifications.

    All config fields (recipients, min_priority, base_url) are resolved
    **per event** from the live ``config`` object so in-process config
    mutations take effect on the next notification. Config-file edits
    still require a restart — there's no file-reload mechanism today.
    """

    async def _deliver(record: NotificationRecord,
                       recipients: list[str], base_url: str) -> None:
        """Background-task delivery — fire-and-forget from the handler."""
        from decafclaw.mail import send_mail
        body = _format_body(record, base_url)
        subject = _format_subject(config.agent.id, record)
        try:
            await send_mail(
                config, to=recipients, subject=subject, body=body,
            )
        except Exception as exc:
            log.warning(
                "Email notification delivery failed (recipients=%s "
                "category=%s priority=%s conv=%s): %s",
                recipients, record.category, record.priority,
                record.conv_id or "-", exc,
            )

    async def handle(event: dict) -> None:
        if event.get("type") != "notification_created":
            return
        # Per-event config read — see mattermost_dm.py for the rationale.
        # Also strip + filter recipients here so whitespace from JSON
        # config doesn't produce phantom entries.
        channel_cfg = config.notifications.channels.email
        email_cfg = config.email
        recipients = [
            r.strip() for r in channel_cfg.recipient_addresses
            if r and r.strip()
        ]
        if (not channel_cfg.enabled
                or not recipients
                or not email_cfg.enabled
                or not email_cfg.smtp_host
                or not (email_cfg.sender_address or "").strip()):
            return
        record = NotificationRecord.from_dict(event["record"])
        if not _meets_priority(record.priority, channel_cfg.min_priority):
            return
        base_url = config.http.base_url
        asyncio.create_task(_deliver(record, recipients, base_url))

    return handle
