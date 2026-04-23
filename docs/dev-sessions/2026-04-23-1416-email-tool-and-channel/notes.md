# Session Notes

## 2026-04-23

- Session started. Closes [#231](https://github.com/lmorchard/decafclaw/issues/231).
- Dual-surface: agent tool (`send_email`) + notification channel adapter, sharing an SMTP core.
- Runs after #315 (notification channel adapters / Mattermost DM) — reuses the same event-bus subscriber pattern.
- Lightweight session like the adapter one: brainstorm + spec, skip `plan.md`, execute in phases.

## Brainstorm — Q&A trail

- **Q1: Tool confirmation model.** Options: confirm-always (A) / allowlist-only (B) / confirm by default, allowlist bypasses (C). **Landed on C** — mirrors `check_shell_approval`, covers both interactive and scheduled-task use cases. Allowlist sources: global `config.email.allowed_recipients` + per-task scheduled-task frontmatter `email-recipients`. Entry shape: exact addresses or `@domain.com` suffix patterns (no regex).
- **Q2: Channel allowlist.** Options: channel bypasses allowlist (A) / channel goes through same check (B). **Landed on A** — config-provided `recipient_addresses` IS the trust boundary. Also: `recipient_addresses` is a list, not a single string.
- **Q3: Async library.** `aiosmtplib` (A) vs stdlib `smtplib` + `asyncio.to_thread` (B). **Landed on A** — matches the async-throughout convention, cleaner call sites, one new dep.
- **Q4: Body format.** Plain only (A) / plain + optional HTML (B) / markdown → auto-HTML (C). **Landed on B** — `multipart/alternative` when both present, plain otherwise. Notification channel is plain-text only (no HTML envelope). Agent can hand-render markdown to HTML as needed.
- **Q5: Attachments.** In scope now (A) / deferred (B). **Landed on A** — useful enough that it belongs in the first cut. Workspace-relative paths, resolve + sandbox check, mimetypes sniff, size cap from new `email.max_attachment_bytes` (default 10 MB).
- **Q6: Transport / auth.** **STARTTLS on 587 with plain SMTP AUTH only.** No SMTPS (implicit TLS on 465), no OAuth2. Covers ~99% of modern providers (Gmail, Fastmail, SendGrid, Postfix, etc.) with app-specific passwords.
- **Q7: From address override.** Always `config.email.sender_address` (A) / sender address + agent can set display name (B) / full override (C). **Landed on A** — simplest, safest, matches "bot has one identity." Revisit if persona use case surfaces.
- **Q8: Test strategy.** Mock-only (A) / mocks + aiosmtpd end-to-end (B). **Landed on B**, with a refinement: the aiosmtpd tests are marked `@pytest.mark.integration` so they stay off the default `make test` path and out of CI, matching the policy for the real-LLM integration tests. Even though aiosmtpd is a local fake server (no credentials), the policy is "anything that's not a pure mock gets the integration marker."

All 8 questions landed in a single brainstorm pass. Skipping `plan.md` per the lightweight-session convention.
