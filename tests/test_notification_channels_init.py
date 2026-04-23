"""Tests for the consolidated notification-channel wiring
(``notification_channels.init_notification_channels``).

Closes #317. Previously each channel's startup guard + subscribe call
was inlined in ``runner.py``; the consolidation lets new channels land
in their own module without a runner.py edit.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from decafclaw.notification_channels import init_notification_channels


def _enable_mm_dm(config, recipient="les"):
    config.notifications.channels.mattermost_dm.enabled = True
    config.notifications.channels.mattermost_dm.recipient_username = recipient


def _enable_email(config, recipients=("ops@example.com",)):
    config.email.enabled = True
    config.email.smtp_host = "smtp.example.com"
    config.email.sender_address = "bot@example.com"
    config.notifications.channels.email.enabled = True
    config.notifications.channels.email.recipient_addresses = list(recipients)


class TestInitNotificationChannels:
    def test_no_channels_enabled_nothing_subscribed(self, config):
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_mm_dm_subscribed_when_configured(self, config):
        _enable_mm_dm(config)
        bus = MagicMock()
        mm_client = MagicMock()
        init_notification_channels(config, bus, mm_client=mm_client)
        assert bus.subscribe.call_count == 1

    def test_mm_dm_skipped_when_no_client(self, config):
        """Channel config is complete but Mattermost client is None."""
        _enable_mm_dm(config)
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_mm_dm_skipped_when_recipient_empty(self, config):
        _enable_mm_dm(config, recipient="")
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=MagicMock())
        bus.subscribe.assert_not_called()

    def test_email_subscribed_when_configured(self, config):
        _enable_email(config)
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        assert bus.subscribe.call_count == 1

    def test_email_skipped_when_core_disabled(self, config):
        """Channel config is complete but `email.enabled` is false."""
        _enable_email(config)
        config.email.enabled = False
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_email_skipped_when_no_smtp_host(self, config):
        _enable_email(config)
        config.email.smtp_host = ""
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_email_skipped_when_sender_address_missing(self, config):
        _enable_email(config)
        config.email.sender_address = ""
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_email_skipped_when_no_recipients(self, config):
        _enable_email(config, recipients=[])
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=None)
        bus.subscribe.assert_not_called()

    def test_both_channels_subscribed(self, config):
        _enable_mm_dm(config)
        _enable_email(config)
        bus = MagicMock()
        init_notification_channels(config, bus, mm_client=MagicMock())
        assert bus.subscribe.call_count == 2
