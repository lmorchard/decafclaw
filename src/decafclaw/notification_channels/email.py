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

from decafclaw import notifications
from decafclaw.notification_escalation import EscalationScheduler
from decafclaw.notifications import NotificationRecord

from . import PRIORITY_GLYPH, meets_priority, should_defer

log = logging.getLogger(__name__)


def _format_body(record: NotificationRecord, base_url: str) -> str:
    """Render a notification as a short plain-text email body.

    Same shape as the Mattermost DM body — priority glyph + title +
    body + optional link line — but without any markdown escaping
    since email clients render plain text literally.
    """
    glyph = PRIORITY_GLYPH.get(record.priority, "🔔")
    parts = [f"{glyph} {record.title}"]
    if record.body:
        parts.append(record.body)
    link = _resolve_link(record, base_url)
    if link:
        parts.append(f"-> {link}")
    return "\n".join(parts)


def _resolve_link(record: NotificationRecord, base_url: str) -> str | None:
    if record.link and (record.link.startswith("http://") or record.link.startswith("https://")):
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


def _active_recipients(channel_cfg: Any, email_cfg: Any) -> list[str]:
    """Recipients for the current config, or [] when the channel cannot run."""
    recipients = [r.strip() for r in channel_cfg.recipient_addresses if r and r.strip()]
    if (
        not channel_cfg.enabled
        or not recipients
        or not email_cfg.enabled
        or not email_cfg.smtp_host
        or not (email_cfg.sender_address or "").strip()
    ):
        return []
    return recipients


async def _deliver_record(config: Any, record: NotificationRecord) -> None:
    """Send one record, resolving recipients/base URL at delivery time."""
    channel_cfg = config.notifications.channels.email
    recipients = _active_recipients(channel_cfg, config.email)
    if not recipients:
        return
    from decafclaw.mail import send_mail

    body = _format_body(record, config.http.base_url)
    subject = _format_subject(config.agent.id, record)
    try:
        await send_mail(
            config,
            to=recipients,
            subject=subject,
            body=body,
        )
    except Exception as exc:
        log.warning(
            "Email notification delivery failed (recipients=%s category=%s priority=%s conv=%s): %s",
            recipients,
            record.category,
            record.priority,
            record.conv_id or "-",
            exc,
        )


def make_email_adapter(
    config: Any,
    scheduler: EscalationScheduler | None = None,
) -> Callable[[dict], Awaitable[None]]:
    """Return an event-bus handler that emails notifications.

    All config fields (recipients, min_priority, base_url) are resolved
    **per event** from the live ``config`` object so in-process config
    mutations take effect on the next notification. Config-file edits
    still require a restart — there's no file-reload mechanism today.

    With a ``scheduler``, a ``delivery: "deferred"`` channel arms a durable
    escalation instead of sending at publish time (#1664).
    """

    async def deliver_deferred(payload: dict) -> None:
        record = notifications.find_record(config, str(payload.get("record_id", "")))
        if record is None:
            log.info("Deferred email push dropped: inbox record %r no longer live", payload.get("record_id"))
            return
        if notifications.is_read(config, record.id):
            log.info("Deferred email push cancelled: record %s was read", record.id)
            return
        await _deliver_record(config, record)

    if scheduler is not None:
        scheduler.register("email", deliver_deferred)

    async def handle(event: dict) -> None:
        if event.get("type") != "notification_created":
            return
        # Per-event config read — see mattermost_dm.py for the rationale.
        # Also strip + filter recipients here so whitespace from JSON
        # config doesn't produce phantom entries.
        channel_cfg = config.notifications.channels.email
        recipients = _active_recipients(channel_cfg, config.email)
        if not recipients:
            return
        record = NotificationRecord.from_dict(event["record"])
        if not meets_priority(record.priority, channel_cfg.min_priority):
            return
        if scheduler is not None and should_defer(channel_cfg, record):
            scheduler.arm("email", record.id, channel_cfg.escalate_after_sec, {"record_id": record.id})
            return
        asyncio.create_task(_deliver_record(config, record))

    return handle
