# Spec — Email Tool + Notification Channel

## Context

Closes [#231](https://github.com/lmorchard/decafclaw/issues/231). Dual-surface feature:

1. **Agent tool** — `send_email(to, subject, body, ...)` that the agent can call during interactive turns, scheduled tasks, or background work. Confirmation-gated with a recipient allowlist that bypasses confirmation for pre-approved recipients.
2. **Notification channel adapter** — second channel after Mattermost DM (shipped in #315). Subscribes to `notification_created`, formats the record, emails it.

Both surfaces share a single async SMTP core so configuration, auth, and connection handling live in one place.

---

## Architecture

```
src/decafclaw/
    mail.py                        # shared async SMTP core (aiosmtplib)
    tools/
        email_tools.py             # send_email tool + allowlist check
    notification_channels/
        email.py                   # channel adapter subscriber
```

### Mail core — `src/decafclaw/mail.py`

Thin async wrapper over `aiosmtplib`. Single entry point:

```python
async def send_mail(
    config,
    *,
    to: str | list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> None: ...
```

- Builds an `email.message.EmailMessage` (stdlib) — adds attachments via `add_attachment()` with `mimetypes.guess_type()` for content-type sniffing.
- Connects to `config.email.smtp_host:smtp_port`, does STARTTLS if `use_tls`, authenticates with `smtp_username` / `smtp_password`, sends, quits.
- `From:` is always `config.email.sender_address`. No override.
- Raises on SMTP error; callers (tool + channel) handle logging and retry policy.
- `aiosmtplib` is a new runtime dep — add to `pyproject.toml`.

### Agent tool — `src/decafclaw/tools/email_tools.py`

`send_email(ctx, to, subject, body, html_body=None, attachments=None)`.

**Allowlist + confirmation flow** — mirrors `check_shell_approval`:

1. Normalize `to`: if passed as `str`, wrap into `[to]`. If empty list → `ToolResult` error.
2. Reject early if `config.email.enabled` is false or `smtp_host` is empty → "email is not configured" error.
3. Resolve the allowlist union:
   - Global: `config.email.allowed_recipients`
   - Per-task overlay: `ctx.tools.preapproved_email_recipients` (populated from scheduled-task frontmatter; empty for interactive runs)
4. `_recipient_allowed(addr, allowlist)` — matches exact addresses *and* `@domain.com` suffix patterns. Case-insensitive. No regex.
5. **All recipients must pass the allowlist to skip confirmation.** If *every* `to` entry matches → send directly. If *any* entry doesn't match → request confirmation showing ALL recipients (so the user sees the full send). Denied / timed out → tool returns a `ToolResult` error, no send.
6. Confirmation message shape:
   ```
   Email to: alice@example.com, bob@example.com
   Subject: Weekly digest
   2 attachments (1.2 MB)
   ---
   <body preview truncated to 200 chars>
   ```
7. Validate attachments BEFORE calling the SMTP core:
   - Each path resolves under `config.workspace_path` (no symlink escapes, no `..`).
   - **Sum** of all attachment file sizes ≤ `config.email.max_attachment_bytes` (default 10 MB).
   - Reject with a clear error message on either check — no partial send.
8. `await mail.send_mail(config, to=<list>, subject=subject, body=body, html_body=html_body, attachments=attachments)`.
9. On SMTP exception: `ToolResult(text="[error: email send failed: {...}]")`. No retry.

**No heartbeat auto-approve.** Shell has `ctx.user_id == "heartbeat-admin"` → auto-approve; email is externally visible so we skip that shortcut. Bypass only via the allowlist.

**Tool definition** declared `critical` priority? No — `normal`. Email isn't a checklist primitive; it's occasional. The allowlist is the primary guardrail; confirmation is the backup.

### Notification channel — `src/decafclaw/notification_channels/email.py`

`make_email_adapter(config) -> Callable`. Same factory shape as Mattermost DM; no extra deps needed beyond `mail.send_mail`.

- Handler filters `notification_created` events by `enabled`, non-empty `recipient_addresses`, and `min_priority`.
- Resolves all config per event (same as MM DM — no captured/reread split-brain).
- Formats a simple plain-text body: priority glyph + title, body (if any), link line (if `base_url` + `conv_id`). *No HTML envelope for channel sends.* Same shape as the MM DM body.
- Fire-and-forget: `asyncio.create_task(_deliver(record, recipients))` so `notify()` doesn't wait on SMTP.
- `_deliver` catches exceptions, logs warning. **The channel does NOT consult `config.email.allowed_recipients`** — the channel's `recipient_addresses` list is the trust boundary.

### Runner wiring

`runner.py` subscribes the email adapter after the Mattermost DM adapter, with its own guard:

```python
email_ch_cfg = config.notifications.channels.email
if (email_ch_cfg.enabled
        and email_ch_cfg.recipient_addresses
        and config.email.enabled
        and config.email.smtp_host):
    from .notification_channels.email import make_email_adapter
    adapter = make_email_adapter(config)
    app_ctx.event_bus.subscribe(adapter)
    log.info(
        "Notifications: email adapter subscribed "
        "(recipients=%s, min_priority=%s)",
        email_ch_cfg.recipient_addresses, email_ch_cfg.min_priority,
    )
```

Three-way guard again: channel enabled + non-empty recipient list + `email.enabled` with a host configured. Missing any piece → adapter isn't subscribed.

### Scheduled-task frontmatter

New optional field:

```yaml
---
schedule: "0 9 * * 1"
allowed-tools: send_email
email-recipients:
  - digest@mozilla.com
  - "@team.mozilla.com"
---
```

Parsed in `schedules.py::parse_schedule_file()` into `ScheduleTask.email_recipients: list[str]`. `run_schedule_task` threads it into `Context.for_task(..., preapproved_email_recipients=...)`. Stored on `ctx.tools.preapproved_email_recipients`.

This mirrors the existing `shell_patterns` → `ctx.tools.preapproved_shell_patterns` plumbing exactly.

---

## Configuration

### `config.email` (new top-level group)

```python
@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = field(default="", metadata={"secret": True})
    smtp_password: str = field(default="", metadata={"secret": True})
    use_tls: bool = True                     # STARTTLS on port 587
    sender_address: str = ""                 # From:
    allowed_recipients: list[str] = field(default_factory=list)
    max_attachment_bytes: int = 10 * 1024 * 1024  # 10 MB
```

Added to top-level `Config` as `email: EmailConfig = field(default_factory=EmailConfig)`. Loaded via existing `load_sub_config` with env prefix `EMAIL`.

### `config.notifications.channels.email` (new sub-sub-group)

```python
@dataclass
class EmailChannelConfig:
    enabled: bool = False
    recipient_addresses: list[str] = field(default_factory=list)
    min_priority: str = "high"
```

Added to `NotificationsChannelsConfig` alongside `mattermost_dm`.

---

## Scope

- [ ] `EmailConfig` + `EmailChannelConfig` dataclasses in `config_types.py`; nested field + runtime loading.
- [ ] `aiosmtplib` runtime dep + `aiosmtpd` dev dep in `pyproject.toml`.
- [ ] `src/decafclaw/mail.py` — async `send_mail()` core.
- [ ] `src/decafclaw/tools/email_tools.py` — `send_email` tool + allowlist + confirmation + attachment sandbox. Register in core tool registry.
- [ ] `src/decafclaw/notification_channels/email.py` — channel adapter factory.
- [ ] `runner.py` — subscribe the adapter with the 3-way guard.
- [ ] `schedules.py` — parse `email-recipients` frontmatter; thread through `ScheduleTask` and `Context.for_task`.
- [ ] `context.py` — add `preapproved_email_recipients: list[str]` to `ToolState`.
- [ ] Tests (mock-based, default path):
  - Mail core: config wiring, `EmailMessage` construction (plain / plain+HTML / with attachments), SMTP call sequence.
  - Tool: allowlist matching (exact + `@domain`), confirmation-bypass path, confirmation-required path, denied confirmation, attachment path traversal rejection, attachment size cap.
  - Notification channel: priority filter, enabled gate, recipient list gate, fire-and-forget dispatch, exception swallow.
  - Schedule frontmatter: `email-recipients` parsed + propagated to `ctx.tools`.
  - Config: defaults + JSON loading for both config groups.
- [ ] Integration tests (`@pytest.mark.integration`, excluded from CI by default, runnable via `make test-integration`):
  - `aiosmtpd`-based end-to-end: tool sends a message, fake server receives it, body + attachment decode correctly.
- [ ] Docs:
  - New `docs/email.md` — tool usage, allowlist semantics, scheduled-task integration, SMTP config.
  - `docs/notifications.md` — new "Email channel" subsection (twin of the MM DM section).
  - `docs/config.md` — `email` + `notifications.channels.email` entries.
  - `CLAUDE.md` — key files additions; convention bullets for the allowlist pattern + channel.
  - `docs/schedules.md` — mention `email-recipients` frontmatter field.

---

## Non-goals (this session)

- Inbound email / reply handling
- Thread tracking, `Message-ID` chains
- Jinja2 / markdown templating — the tool accepts rendered bodies
- Rate limiting beyond whatever the SMTP provider enforces
- Multi-user
- OAuth2 / XOAUTH2
- Implicit-TLS / SMTPS on port 465
- Per-send `From:` override
- Display-name persona
- Deferred / queued send (fire-and-forget at the event-bus level, but no durable retry queue)

---

## Brainstorm decisions (for the record)

See `notes.md` for the Q&A trail. In short:

1. **Tool confirmation = Option C** — confirmation by default, allowlist bypasses. Allowlist from `config.email.allowed_recipients` + scheduled-task frontmatter `email-recipients`. Entries match exact addresses or `@domain.com` suffixes; no regex.
2. **Channel allowlist = Option A** — channel has its own `recipient_addresses` list; does NOT consult the tool's `allowed_recipients`. Config-provided recipient IS the trust boundary.
3. **Async lib = `aiosmtplib`** — native async, one new dep, cleaner than stdlib + `asyncio.to_thread`.
4. **Body format = plain + optional HTML** — MIME `multipart/alternative`. Notification channel is plain-text only.
5. **Attachments = in scope** — workspace-relative paths, path sandbox, mimetypes sniff, size cap.
6. **Transport + auth = STARTTLS on 587, plain SMTP AUTH** only. No implicit TLS, no OAuth2.
7. **From address = always `config.email.sender_address`**. No per-send override.
8. **Tests = mock-heavy + aiosmtpd end-to-end under `@pytest.mark.integration`** — the fake-server tests actually open a socket, so they match the existing integration-test policy (off the default path, not run in CI, runnable via `make test-integration`).
