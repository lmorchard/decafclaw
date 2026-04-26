"""Tests for the email notification channel adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw import notifications as notifs
from decafclaw.events import EventBus
from decafclaw.notification_channels import meets_priority as _meets_priority
from decafclaw.notification_channels.email import (
    _format_body,
    _format_subject,
    make_email_adapter,
)


def _rec(**overrides) -> notifs.NotificationRecord:
    return notifs.NotificationRecord(
        id=overrides.get("id", "abc"),
        timestamp=overrides.get("timestamp", "2026-04-23T12:00:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat"),
        priority=overrides.get("priority", "normal"),
        body=overrides.get("body", ""),
        link=overrides.get("link"),
        conv_id=overrides.get("conv_id"),
    )


def _enable_channel(config, **overrides):
    """Configure both the email core and the email channel."""
    config.email.enabled = True
    config.email.smtp_host = "smtp.example.com"
    config.email.sender_address = "bot@example.com"
    config.notifications.channels.email.enabled = True
    config.notifications.channels.email.recipient_addresses = overrides.get(
        "recipients", ["ops@example.com"],
    )
    config.notifications.channels.email.min_priority = overrides.get(
        "min_priority", "normal",
    )
    return config


class TestFormatBody:
    def test_basic(self):
        body = _format_body(_rec(title="Alert", body="details"), "")
        assert "Alert" in body
        assert "details" in body

    def test_priority_glyph(self):
        assert _format_body(_rec(priority="high"), "").startswith("⚠️")
        assert _format_body(_rec(priority="normal"), "").startswith("🔔")
        assert _format_body(_rec(priority="low"), "").startswith("·")

    def test_link_from_conv_id_and_base_url(self):
        body = _format_body(_rec(conv_id="c-1"), "http://agent.local")
        assert "http://agent.local/#conv=c-1" in body

    def test_no_link_without_base_url(self):
        assert "->" not in _format_body(_rec(conv_id="c-1"), "")

    def test_explicit_http_link_preserved(self):
        body = _format_body(
            _rec(link="https://example.com/x"), "http://agent.local",
        )
        assert "https://example.com/x" in body


class TestFormatSubject:
    def test_agent_then_category_then_title(self):
        assert _format_subject(
            "decafclaw",
            _rec(category="heartbeat", title="OK"),
        ) == "[decafclaw] [heartbeat] OK"

    def test_agent_id_appears_first(self):
        """The agent-id prefix is load-bearing for inbox filtering —
        it should be the leftmost bracket group so mail-rule matchers
        can anchor on it."""
        subject = _format_subject("other-bot", _rec(category="t", title="x"))
        assert subject.startswith("[other-bot] ")


class TestMeetsPriority:
    def test_threshold(self):
        assert _meets_priority("high", "normal")
        assert _meets_priority("normal", "normal")
        assert not _meets_priority("low", "normal")


class TestAdapterHandler:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_email(self, config):
        _enable_channel(config, recipients=["ops@example.com"])
        handler = make_email_adapter(config)

        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high", title="Alert").to_dict(),
            })
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["to"] == ["ops@example.com"]
        assert "Alert" in kwargs["subject"]
        assert "Alert" in kwargs["body"]

    @pytest.mark.asyncio
    async def test_ignores_non_notification_events(self, config):
        _enable_channel(config)
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({"type": "tool_start", "context_id": "x"})
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_channel_disabled(self, config):
        _enable_channel(config)
        config.notifications.channels.email.enabled = False
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high").to_dict(),
            })
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_email_core_disabled(self, config):
        _enable_channel(config)
        config.email.enabled = False
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high").to_dict(),
            })
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_sender_address_missing(self, config):
        """Per-event guard catches runtime config changes where
        sender_address was cleared after subscribe.
        """
        _enable_channel(config)
        config.email.sender_address = ""
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high").to_dict(),
            })
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_recipients(self, config):
        _enable_channel(config, recipients=[])
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high").to_dict(),
            })
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_priority_filter(self, config):
        _enable_channel(config, min_priority="high")
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await handler({
                "type": "notification_created",
                "record": _rec(priority="normal").to_dict(),
            })
            await asyncio.sleep(0)
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_delivery_errors_are_swallowed(self, config, caplog):
        _enable_channel(config)
        handler = make_email_adapter(config)
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = RuntimeError("smtp down")
            await handler({
                "type": "notification_created",
                "record": _rec(priority="high").to_dict(),
            })
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert any("Email notification delivery failed" in r.message
                   for r in caplog.records)


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_notify_triggers_adapter_via_eventbus(self, config):
        _enable_channel(config)
        bus = EventBus()
        bus.subscribe(make_email_adapter(config))
        with patch("decafclaw.mail.send_mail", new_callable=AsyncMock) as mock_send:
            await notifs.notify(
                config, bus, category="test", title="Ping",
                priority="high",
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        mock_send.assert_awaited_once()
