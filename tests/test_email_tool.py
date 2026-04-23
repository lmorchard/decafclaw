"""Tests for the ``send_email`` agent tool (`decafclaw.tools.email_tools`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.tools.email_tools import (
    _all_recipients_allowed,
    _recipient_allowed,
    _validate_attachments,
    tool_send_email,
)


def _enable_email(config, **overrides):
    config.email.enabled = True
    config.email.smtp_host = overrides.get("smtp_host", "smtp.example.com")
    config.email.smtp_port = 587
    config.email.smtp_username = "user"
    config.email.smtp_password = "pass"
    config.email.sender_address = "bot@example.com"
    config.email.allowed_recipients = overrides.get(
        "allowed_recipients", [],
    )
    return config


# -- Allowlist matching -----------------------------------------------------


class TestRecipientAllowed:
    def test_exact_match(self):
        assert _recipient_allowed("alice@example.com", ["alice@example.com"])

    def test_exact_match_case_insensitive(self):
        assert _recipient_allowed("Alice@Example.COM", ["alice@example.com"])

    def test_exact_no_match(self):
        assert not _recipient_allowed("bob@example.com", ["alice@example.com"])

    def test_domain_suffix(self):
        assert _recipient_allowed("bob@example.com", ["@example.com"])

    def test_domain_suffix_strict_no_subdomain(self):
        """`@example.com` matches the example.com domain only — NOT subdomains.
        This is intentional: allowlist semantics should be strict.
        """
        assert not _recipient_allowed(
            "carol@sub.example.com", ["@example.com"],
        )

    def test_domain_case_insensitive(self):
        assert _recipient_allowed("bob@EXAMPLE.com", ["@example.com"])

    def test_domain_no_match(self):
        assert not _recipient_allowed("bob@other.com", ["@example.com"])

    def test_empty_addr(self):
        assert not _recipient_allowed("", ["@example.com"])

    def test_empty_allowlist(self):
        assert not _recipient_allowed("a@x.com", [])

    def test_mixed_entries(self):
        allowlist = ["admin@corp.com", "@partner.com"]
        assert _recipient_allowed("admin@corp.com", allowlist)
        assert _recipient_allowed("sales@partner.com", allowlist)
        assert not _recipient_allowed("random@elsewhere.com", allowlist)


class TestAllRecipientsAllowed:
    def test_all_match(self):
        assert _all_recipients_allowed(
            ["a@x.com", "b@x.com"], ["@x.com"],
        )

    def test_one_fails(self):
        assert not _all_recipients_allowed(
            ["a@x.com", "c@y.com"], ["@x.com"],
        )

    def test_empty_recipients(self):
        assert not _all_recipients_allowed([], ["@x.com"])


# -- Attachment validation --------------------------------------------------


class TestValidateAttachments:
    def test_empty(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        resolved, err = _validate_attachments(config, [])
        assert resolved == []
        assert err is None

    def test_happy_path(self, config):
        ws = config.workspace_path
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "file.txt").write_text("hi")
        resolved, err = _validate_attachments(config, ["file.txt"])
        assert err is None
        assert len(resolved) == 1
        assert resolved[0].endswith("file.txt")

    def test_rejects_absolute(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        _, err = _validate_attachments(config, ["/etc/passwd"])
        assert err is not None
        assert "relative" in err.lower()

    def test_rejects_escape(self, config, tmp_path):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        _, err = _validate_attachments(config, ["../outside.txt"])
        assert err is not None
        assert "escape" in err.lower() or "workspace" in err.lower()

    def test_missing_file(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        _, err = _validate_attachments(config, ["nope.txt"])
        assert err is not None
        assert "not found" in err.lower()

    def test_size_cap_enforced(self, config):
        ws = config.workspace_path
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "big.bin").write_bytes(b"x" * 100)
        config.email.max_attachment_bytes = 50
        _, err = _validate_attachments(config, ["big.bin"])
        assert err is not None
        assert "exceeds" in err.lower()

    def test_sum_not_per_file(self, config):
        """Cap is the sum across attachments, not per-file."""
        ws = config.workspace_path
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "a").write_bytes(b"x" * 40)
        (ws / "b").write_bytes(b"x" * 40)
        config.email.max_attachment_bytes = 70  # sum (80) > limit
        _, err = _validate_attachments(config, ["a", "b"])
        assert err is not None


# -- Tool integration -------------------------------------------------------


class TestSendEmailTool:
    @pytest.mark.asyncio
    async def test_rejects_when_email_disabled(self, ctx):
        ctx.config.email.enabled = False
        result = await tool_send_email(
            ctx, to="a@x.com", subject="s", body="b",
        )
        assert "not configured" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_when_sender_address_missing(self, ctx):
        """Without a From: address, the send would fail at the SMTP
        level with a less actionable error — surface that at the tool
        boundary instead.
        """
        _enable_email(ctx.config, allowed_recipients=["@x.com"])
        ctx.config.email.sender_address = ""
        result = await tool_send_email(
            ctx, to="a@x.com", subject="s", body="b",
        )
        assert "not configured" in result.text.lower()
        assert "sender_address" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_empty_recipients(self, ctx):
        _enable_email(ctx.config)
        result = await tool_send_email(ctx, to=[], subject="s", body="b")
        assert "no recipients" in result.text.lower()

    @pytest.mark.asyncio
    async def test_allowlist_bypass(self, ctx):
        """All recipients allowlisted → no confirmation, direct send."""
        _enable_email(ctx.config, allowed_recipients=["@x.com"])
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            result = await tool_send_email(
                ctx, to=["a@x.com", "b@x.com"], subject="s", body="b",
            )
        assert "sent" in result.text.lower()
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_per_task_overlay_bypass(self, ctx):
        """Scheduled-task email_recipients also bypass confirmation."""
        _enable_email(ctx.config, allowed_recipients=[])
        ctx.tools.preapproved_email_recipients = ["@team.com"]
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            result = await tool_send_email(
                ctx, to="digest@team.com", subject="s", body="b",
            )
        assert "sent" in result.text.lower()
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_confirmation_required_for_non_allowlisted(self, ctx):
        _enable_email(ctx.config, allowed_recipients=["@safe.com"])
        with (
            patch("decafclaw.tools.email_tools.request_confirmation",
                  new_callable=AsyncMock) as mock_confirm,
            patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send,
        ):
            mock_confirm.return_value = {"approved": True}
            result = await tool_send_email(
                ctx, to="random@elsewhere.com", subject="s", body="b",
            )
        mock_confirm.assert_awaited_once()
        # The confirmation message should include the recipient + subject
        confirm_kwargs = mock_confirm.await_args.kwargs
        assert "random@elsewhere.com" in confirm_kwargs["message"]
        mock_send.assert_awaited_once()
        assert "sent" in result.text.lower()

    @pytest.mark.asyncio
    async def test_confirmation_denied(self, ctx):
        _enable_email(ctx.config, allowed_recipients=[])
        with (
            patch("decafclaw.tools.email_tools.request_confirmation",
                  new_callable=AsyncMock) as mock_confirm,
            patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send,
        ):
            mock_confirm.return_value = {"approved": False}
            result = await tool_send_email(
                ctx, to="random@x.com", subject="s", body="b",
            )
        assert "denied" in result.text.lower()
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_allowlist_still_confirms(self, ctx):
        """If ANY recipient isn't allowlisted, confirm the full batch."""
        _enable_email(ctx.config, allowed_recipients=["@safe.com"])
        with (
            patch("decafclaw.tools.email_tools.request_confirmation",
                  new_callable=AsyncMock) as mock_confirm,
            patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send,
        ):
            mock_confirm.return_value = {"approved": True}
            await tool_send_email(
                ctx, to=["ok@safe.com", "unknown@x.com"],
                subject="s", body="b",
            )
        mock_confirm.assert_awaited_once()
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_smtp_failure_returns_error(self, ctx):
        _enable_email(ctx.config, allowed_recipients=["@x.com"])
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = RuntimeError("smtp down")
            result = await tool_send_email(
                ctx, to="a@x.com", subject="s", body="b",
            )
        assert "failed" in result.text.lower()

    @pytest.mark.asyncio
    async def test_attachment_sandbox_error_blocks_send(self, ctx):
        _enable_email(ctx.config, allowed_recipients=["@x.com"])
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            result = await tool_send_email(
                ctx, to="a@x.com", subject="s", body="b",
                attachments=["/etc/passwd"],
            )
        assert "error" in result.text.lower()
        mock_send.assert_not_called()
