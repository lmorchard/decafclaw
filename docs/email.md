# Email

DecafClaw can send outbound email via SMTP. There are two surfaces:

- **Agent tool** — `send_email(to, subject, body, html_body=None, attachments=None)`. The agent calls this during interactive turns, scheduled tasks, or background work. Each send is gated by an allowlist + confirmation.
- **Notification channel** — second channel alongside the Mattermost DM adapter (see [notifications.md](notifications.md)). Emails every notification at or above `min_priority` to a configured recipient list.

Both surfaces share a single async SMTP core (`src/decafclaw/mail.py`) so configuration, auth, and transport happen in one place.

## SMTP configuration

All SMTP settings live under `config.email`. See [config.md](config.md#email) for the full table.

```json
{
  "email": {
    "enabled": true,
    "smtp_host": "smtp.fastmail.com",
    "smtp_port": 587,
    "smtp_username": "bot@mozilla.com",
    "smtp_password": "<app-specific-password>",
    "use_tls": true,
    "sender_address": "bot@mozilla.com",
    "allowed_recipients": [
      "lorchard@mozilla.com",
      "@mozilla.com"
    ],
    "max_attachment_bytes": 10485760
  }
}
```

**Supported transport:** STARTTLS on port 587 with plain SMTP AUTH. This covers Gmail (with app-specific passwords), Fastmail, SendGrid, Mailgun, self-hosted Postfix, and every mainstream provider. Implicit TLS on port 465 and OAuth2 are not supported — both are deferred to follow-on issues if a real use case surfaces.

**Credentials** (`smtp_username`, `smtp_password`) are marked secret and masked in `make config`. Store them in `data/{agent_id}/config.json` (which is git-ignored by convention) or via `EMAIL_SMTP_USERNAME` / `EMAIL_SMTP_PASSWORD` env vars.

**No auth** — leaving `smtp_username` and `smtp_password` empty is valid for local postfix or similar. The mail core passes `None` for both so `aiosmtplib` skips the AUTH step.

## Agent tool: `send_email`

```python
send_email(
    to="alice@example.com",          # or ["alice@example.com", "bob@example.com"]
    subject="Weekly digest",
    body="Plain text body.",
    html_body="<p>Optional HTML.</p>",
    attachments=["reports/digest.pdf"]  # workspace-relative paths
)
```

**Confirmation flow.** Every send hits a pre-check:

1. The tool builds the allowlist: `config.email.allowed_recipients` + `ctx.tools.preapproved_email_recipients` (the scheduled-task overlay, see below).
2. **If every recipient matches the allowlist → send directly, no prompt.**
3. **If any recipient does NOT match → request interactive confirmation** showing all recipients, subject, attachment summary, and a 200-char body preview. Denied or timed-out → tool returns an error, no send.

Entries in the allowlist match:

- **Exact addresses:** `alice@example.com` matches only that address.
- **Domain suffix:** `@example.com` matches any address at `example.com` (strict — subdomains like `sub.example.com` do NOT match; if you want those, list them explicitly).

Case-insensitive. No regex — the allowlist is meant to be easy to eyeball and hard to misconfigure.

**Attachments** are resolved against the agent workspace:

- Absolute paths, `..` escapes, and symlinks that resolve outside the workspace are all rejected.
- The **sum** of attachment file sizes must fit under `config.email.max_attachment_bytes` (default 10 MB).
- Missing files surface a clean error before the SMTP call — no partial send.

**From address** is always `config.email.sender_address`. The agent can't override it per send. (Persona use cases would require revisiting; file an issue if one comes up.)

**SMTP failures** return a `ToolResult` error with the exception message. The tool does not retry — it's a single fire. If the send matters, you can try again on the next turn.

## Notification channel

Configured under `config.notifications.channels.email`:

```json
{
  "notifications": {
    "channels": {
      "email": {
        "enabled": true,
        "recipient_addresses": ["ops@mozilla.com"],
        "min_priority": "high"
      }
    }
  }
}
```

Defaults match the Mattermost DM channel: disabled, empty recipient list, `min_priority: high` (only urgent events DM/email; routine `normal` events stay in the inbox).

**Startup guard.** The adapter is only subscribed at boot when *all* of:

- `notifications.channels.email.enabled` is true
- `notifications.channels.email.recipient_addresses` is non-empty
- `email.enabled` is true
- `email.smtp_host` is non-empty
- `email.sender_address` is non-empty

Any missing piece → adapter isn't wired; no runtime errors at `notify()` time. Recipient list entries are also trimmed of whitespace at send time, so sloppy JSON config doesn't produce phantom recipients.

**Trust boundary.** The channel's `recipient_addresses` list is the trust boundary. The channel does NOT consult `config.email.allowed_recipients` — that applies only to the agent tool, where the recipient is runtime-chosen and needs a sanity check. Channel recipients are admin-configured at the config-file layer, so asking you to list them twice adds friction without adding safety.

**Body shape.** Plain text only (no HTML). Priority glyph + title on the header line, body (if any) on the next lines, optional link at the bottom (only when `config.http.base_url` is set and the record has a `conv_id`).

Subject is `[<agent_id>] [<category>] <title>` for quick filtering in mail clients. The agent-id prefix (from `config.agent.id`) lets recipients anchor inbox rules on the sender identity even when multiple DecafClaw instances share a mailbox.

**Dispatch.** Fire-and-forget via `asyncio.create_task`. `notify()` returns immediately after the inbox append; the SMTP send happens in a detached task. Delivery failures log a warning and are otherwise swallowed — the inbox record is the source of truth.

## Scheduled-task integration

A scheduled task declares per-task email recipients via frontmatter:

```markdown
---
schedule: "0 9 * * 1"
allowed-tools: send_email
email-recipients:
  - digest@mozilla.com
  - "@team.mozilla.com"
---
Compose the weekly digest and email it to the team.
```

The `email-recipients` entries merge with `config.email.allowed_recipients` only for this task's run — they populate `ctx.tools.preapproved_email_recipients`. Any send by this task whose recipient matches either list bypasses confirmation (which is important because scheduled runs have no user present to click Approve).

Entries follow the same "exact addresses or `@domain` suffix" rules as the global allowlist.

## Testing

Mock-based unit tests under `tests/test_mail.py`, `tests/test_email_tool.py`, and `tests/test_notification_channels_email.py` cover every logic path and run on the default `make test` invocation.

End-to-end wire-format tests live in `tests/test_mail_integration.py` — they spin up a local `aiosmtpd` fake SMTP server, actually send a message, and assert the captured envelope. Marked `@pytest.mark.integration` and excluded from the default suite (matching the real-LLM test policy). Run via `make test-integration`.

## Out of scope (follow-ups)

- Inbound email / reply handling
- Implicit TLS on port 465
- OAuth2 / XOAUTH2 (app-specific passwords are the recommended alternative for Gmail / M365)
- Per-send `From:` override or display-name personas
- Rate limiting beyond whatever the SMTP provider enforces
- Durable send queue with retry (today we fire once, let it fail, rely on the inbox as ground truth)
