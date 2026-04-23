"""``send_email`` agent tool — confirmation-gated email sending.

Recipients matching the configured allowlist
(``config.email.allowed_recipients`` + scheduled-task
``email-recipients`` frontmatter) bypass confirmation. Any recipient
that doesn't match triggers an interactive confirmation prompt that
shows the full send (all recipients, subject, attachment summary, body
preview).

Allowlist entries match exact addresses *or* ``@domain.com`` suffix
patterns, case-insensitive. No regex. See ``docs/email.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from decafclaw.media import ToolResult

from .confirmation import request_confirmation

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Allowlist matching
# ---------------------------------------------------------------------------


def _recipient_allowed(addr: str, allowlist: list[str]) -> bool:
    """Match an address against allowlist entries (case-insensitive).

    Entry shapes:
    - Exact address: ``alice@example.com`` matches only that address.
    - Domain suffix: ``@example.com`` matches any address ending with
      that domain.
    Empty addr or empty allowlist → never allowed.
    """
    if not addr or not allowlist:
        return False
    addr_lower = addr.strip().lower()
    for entry in allowlist:
        entry_lower = entry.strip().lower()
        if not entry_lower:
            continue
        if entry_lower.startswith("@"):
            if addr_lower.endswith(entry_lower):
                return True
        elif addr_lower == entry_lower:
            return True
    return False


def _all_recipients_allowed(recipients: list[str], allowlist: list[str]) -> bool:
    return bool(recipients) and all(
        _recipient_allowed(r, allowlist) for r in recipients
    )


# ---------------------------------------------------------------------------
# Attachment validation
# ---------------------------------------------------------------------------


def _validate_attachments(
    config, attachment_paths: list[str],
) -> tuple[list[str], str | None]:
    """Resolve each attachment under ``config.workspace_path`` and enforce
    the total-size cap.

    Returns ``(resolved_paths, error_message)``. On success, ``error_message``
    is ``None`` and ``resolved_paths`` are absolute strings suitable for
    ``mail.send_mail``. On any failure the error string is filled in and
    ``resolved_paths`` is empty — callers must not send partial.
    """
    if not attachment_paths:
        return [], None

    workspace = config.workspace_path.resolve()
    max_bytes = config.email.max_attachment_bytes
    resolved: list[str] = []
    total_size = 0

    for rel in attachment_paths:
        if not rel or not isinstance(rel, str):
            return [], f"[error: invalid attachment path '{rel}']"
        if Path(rel).is_absolute():
            return [], (
                f"[error: attachment path must be relative to workspace: '{rel}']"
            )
        candidate = (workspace / rel).resolve()
        if not candidate.is_relative_to(workspace):
            return [], (
                f"[error: attachment path '{rel}' escapes workspace]"
            )
        if not candidate.is_file():
            return [], f"[error: attachment not found: '{rel}']"
        size = candidate.stat().st_size
        total_size += size
        if total_size > max_bytes:
            return [], (
                f"[error: attachments total {total_size} bytes exceeds "
                f"max {max_bytes}]"
            )
        resolved.append(str(candidate))

    return resolved, None


# ---------------------------------------------------------------------------
# Confirmation helper
# ---------------------------------------------------------------------------


def _format_confirmation_message(
    recipients: list[str], subject: str, body: str,
    attachment_count: int, attachment_bytes: int,
) -> str:
    lines = [
        f"Email to: {', '.join(recipients)}",
        f"Subject: {subject}",
    ]
    if attachment_count:
        kb = attachment_bytes / 1024
        lines.append(f"{attachment_count} attachment(s) ({kb:.1f} KB)")
    lines.append("---")
    preview = body[:200] + ("..." if len(body) > 200 else "")
    lines.append(preview)
    return "\n".join(lines)


async def check_email_approval(
    ctx, recipients: list[str], subject: str, body: str,
    attachment_count: int = 0, attachment_bytes: int = 0,
) -> dict:
    """Return ``{"approved": bool}`` for an outbound email.

    Bypasses confirmation when every recipient is in the
    ``config.email.allowed_recipients`` + ``ctx.tools.preapproved_email_recipients``
    union. Otherwise requests interactive confirmation showing the full
    send.
    """
    allowlist = list(ctx.config.email.allowed_recipients) + list(
        ctx.tools.preapproved_email_recipients
    )
    if _all_recipients_allowed(recipients, allowlist):
        log.info("[send_email] pre-approved by allowlist: %s", recipients)
        return {"approved": True}

    message = _format_confirmation_message(
        recipients, subject, body, attachment_count, attachment_bytes,
    )
    return await request_confirmation(
        ctx, tool_name="send_email",
        command=f"email to {', '.join(recipients)}",
        message=message,
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


async def tool_send_email(
    ctx, to: str | list[str], subject: str, body: str,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> ToolResult:
    """Send an email. Confirmation-gated unless all recipients are allowlisted."""
    log.info("[tool:send_email] requested: to=%r subject=%r", to, subject)

    cfg = ctx.config.email
    if not cfg.enabled or not cfg.smtp_host or not (cfg.sender_address or "").strip():
        return ToolResult(
            text="[error: email is not configured (set config.email.enabled, "
                 "smtp_host, and sender_address)]"
        )

    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r for r in (r.strip() for r in recipients) if r]
    if not recipients:
        return ToolResult(text="[error: no recipients specified]")

    resolved_attachments, err = _validate_attachments(
        ctx.config, attachments or [],
    )
    if err:
        return ToolResult(text=err)
    attachment_bytes = sum(
        Path(p).stat().st_size for p in resolved_attachments
    )

    approval = await check_email_approval(
        ctx, recipients, subject, body,
        attachment_count=len(resolved_attachments),
        attachment_bytes=attachment_bytes,
    )
    if not approval.get("approved"):
        return ToolResult(text="[error: email send was denied by user]")

    from decafclaw.mail import send_mail
    try:
        await send_mail(
            ctx.config, to=recipients, subject=subject, body=body,
            html_body=html_body, attachments=resolved_attachments,
        )
    except Exception as exc:
        log.warning("Email send failed: %s", exc)
        return ToolResult(text=f"[error: email send failed: {exc}]")

    att_note = (
        f" with {len(resolved_attachments)} attachment(s)"
        if resolved_attachments else ""
    )
    return ToolResult(
        text=f"Email sent to {', '.join(recipients)}{att_note}.",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

EMAIL_TOOLS = {"send_email": tool_send_email}

EMAIL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email via the configured SMTP provider. "
                "DESTRUCTIVE — delivers an external message. Requires "
                "user confirmation unless every recipient is in the "
                "configured allowlist (either global "
                "`email.allowed_recipients` or, for scheduled tasks, the "
                "task's `email-recipients` frontmatter). "
                "Supports plain text, optional HTML alternative, and "
                "file attachments (paths relative to the workspace, "
                "summed size capped by `email.max_attachment_bytes`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "description": (
                            "Single recipient address or list of addresses."
                        ),
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line (plain text).",
                    },
                    "body": {
                        "type": "string",
                        "description": "Plain-text message body.",
                    },
                    "html_body": {
                        "type": "string",
                        "description": (
                            "Optional HTML alternative. When present the "
                            "email is sent as multipart/alternative so "
                            "clients pick their preferred rendering."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of workspace-relative file paths "
                            "to attach. Paths must resolve under the agent "
                            "workspace; summed size must fit under "
                            "`email.max_attachment_bytes` (default 10 MB)."
                        ),
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]
