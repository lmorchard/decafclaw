"""Tests for the shared SMTP mail core (`decafclaw.mail`).

Mock-based — verify the EmailMessage construction and the
`aiosmtplib.send` call args. End-to-end wire-format coverage is in the
`@pytest.mark.integration` aiosmtpd tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.mail import send_mail


def _configure_email(config, **overrides):
    config.email.enabled = True
    config.email.smtp_host = overrides.get("smtp_host", "smtp.example.com")
    config.email.smtp_port = overrides.get("smtp_port", 587)
    config.email.smtp_username = overrides.get("smtp_username", "user")
    config.email.smtp_password = overrides.get("smtp_password", "pass")
    config.email.use_tls = overrides.get("use_tls", True)
    config.email.sender_address = overrides.get("sender_address", "bot@example.com")
    return config


class TestSendMail:
    @pytest.mark.asyncio
    async def test_plain_text(self, config):
        _configure_email(config)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(
                config, to="alice@example.com",
                subject="hello", body="world",
            )
        assert mock_send.await_count == 1
        msg = mock_send.await_args.args[0]
        assert msg["From"] == "bot@example.com"
        assert msg["To"] == "alice@example.com"
        assert msg["Subject"] == "hello"
        assert "world" in msg.get_content()
        assert not msg.is_multipart()  # plain only

    @pytest.mark.asyncio
    async def test_single_str_to_becomes_list(self, config):
        _configure_email(config)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(config, to="a@x.com", subject="s", body="b")
        msg = mock_send.await_args.args[0]
        assert msg["To"] == "a@x.com"

    @pytest.mark.asyncio
    async def test_list_to_is_comma_joined(self, config):
        _configure_email(config)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(
                config, to=["a@x.com", "b@x.com"],
                subject="s", body="b",
            )
        msg = mock_send.await_args.args[0]
        assert msg["To"] == "a@x.com, b@x.com"

    @pytest.mark.asyncio
    async def test_html_body_makes_alternative(self, config):
        _configure_email(config)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(
                config, to="a@x.com", subject="s",
                body="plain", html_body="<p>html</p>",
            )
        msg = mock_send.await_args.args[0]
        assert msg.is_multipart()
        subtypes = {p.get_content_type() for p in msg.iter_parts()}
        assert subtypes == {"text/plain", "text/html"}

    @pytest.mark.asyncio
    async def test_attachment_added(self, config, tmp_path):
        _configure_email(config)
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        att = tmp_path / "report.txt"
        att.write_text("contents")
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(
                config, to="a@x.com", subject="s", body="b",
                attachments=[str(att)],
            )
        msg = mock_send.await_args.args[0]
        # Has at least one attachment part with the right filename
        atts = [p for p in msg.iter_attachments()]
        assert len(atts) == 1
        assert atts[0].get_filename() == "report.txt"

    @pytest.mark.asyncio
    async def test_smtp_args_wired(self, config):
        _configure_email(config, smtp_host="smtp.test", smtp_port=2525)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(config, to="a@x.com", subject="s", body="b")
        kwargs = mock_send.await_args.kwargs
        assert kwargs["hostname"] == "smtp.test"
        assert kwargs["port"] == 2525
        assert kwargs["username"] == "user"
        assert kwargs["password"] == "pass"
        assert kwargs["start_tls"] is True

    @pytest.mark.asyncio
    async def test_empty_auth_passes_none(self, config):
        """Empty username/password should pass as None, not empty strings,
        so aiosmtplib skips the AUTH step (useful for local postfix)."""
        _configure_email(config, smtp_username="", smtp_password="")
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(config, to="a@x.com", subject="s", body="b")
        kwargs = mock_send.await_args.kwargs
        assert kwargs["username"] is None
        assert kwargs["password"] is None

    @pytest.mark.asyncio
    async def test_empty_sender_raises(self, config):
        """Defense in depth — core refuses to send without a From:."""
        _configure_email(config, sender_address="")
        with pytest.raises(ValueError, match="sender_address"):
            await send_mail(config, to="a@x.com", subject="s", body="b")

    @pytest.mark.asyncio
    async def test_whitespace_sender_raises(self, config):
        _configure_email(config, sender_address="   ")
        with pytest.raises(ValueError, match="sender_address"):
            await send_mail(config, to="a@x.com", subject="s", body="b")

    @pytest.mark.asyncio
    async def test_empty_recipients_raises(self, config):
        _configure_email(config)
        with pytest.raises(ValueError, match="recipient"):
            await send_mail(config, to=[], subject="s", body="b")

    @pytest.mark.asyncio
    async def test_whitespace_recipients_filtered_out(self, config):
        """Blank entries from sloppy JSON config are stripped; at least
        one real address must remain."""
        _configure_email(config)
        with pytest.raises(ValueError, match="recipient"):
            await send_mail(config, to=["", "   "], subject="s", body="b")

    @pytest.mark.asyncio
    async def test_recipients_stripped(self, config):
        """Leading/trailing whitespace is trimmed before the header is built."""
        _configure_email(config)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_mail(
                config, to=[" a@x.com ", "b@x.com\n"],
                subject="s", body="b",
            )
        msg = mock_send.await_args.args[0]
        assert msg["To"] == "a@x.com, b@x.com"
