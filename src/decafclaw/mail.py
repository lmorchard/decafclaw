"""SMTP mail core — shared by the ``send_email`` tool and the email notification channel.

Thin async wrapper over :mod:`aiosmtplib`. Builds a stdlib
:class:`email.message.EmailMessage` (supports plain text, plain +
optional HTML alternative, and attachments) and hands it to aiosmtplib
for transport. STARTTLS on port 587 with plain SMTP AUTH covers every
modern provider (Gmail / Fastmail / SendGrid / Postfix / etc.) given an
app-specific password; OAuth2 and implicit-TLS are out of scope for now.

Callers (tool + channel) are responsible for their own validation
(allowlist, attachment sandbox, priority filter). This module is pure
delivery.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib

log = logging.getLogger(__name__)


async def send_mail(
    config,
    *,
    to: str | list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> None:
    """Send an email via SMTP.

    Args:
        config: Config with a populated ``email`` group (smtp_host,
            smtp_port, smtp_username, smtp_password, use_tls,
            sender_address).
        to: single address or list of addresses. Empty / whitespace-only
            entries are dropped; ``ValueError`` raised if nothing's left.
        subject: plain text subject line.
        body: plain-text body.
        html_body: optional HTML alternative; when provided the message
            becomes ``multipart/alternative`` and clients render their
            preferred form.
        attachments: optional list of file paths (absolute or relative).
            This module does not sandbox paths — callers must validate
            before passing in.

    Raises ``ValueError`` if ``config.email.sender_address`` is empty or
    no valid recipients are supplied. Propagates any
    ``aiosmtplib.SMTPException`` on transport failure.
    """
    cfg = config.email
    sender = (cfg.sender_address or "").strip()
    if not sender:
        raise ValueError(
            "config.email.sender_address must be non-empty"
        )

    raw_recipients = [to] if isinstance(to, str) else list(to)
    recipients = [
        r.strip() for r in raw_recipients if r and r.strip()
    ]
    if not recipients:
        raise ValueError("at least one recipient is required")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for path_str in attachments or []:
        path = Path(path_str)
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        data = await asyncio.to_thread(path.read_bytes)
        msg.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=path.name,
        )

    log.info(
        "email: sending to=%s subject=%r attachments=%d via %s:%d",
        recipients, subject, len(attachments or []),
        cfg.smtp_host, cfg.smtp_port,
    )

    await aiosmtplib.send(
        msg,
        hostname=cfg.smtp_host,
        port=cfg.smtp_port,
        username=cfg.smtp_username or None,
        password=cfg.smtp_password or None,
        start_tls=cfg.use_tls,
    )
