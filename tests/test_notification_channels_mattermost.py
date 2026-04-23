"""Tests for the Mattermost DM notification channel adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from decafclaw import notifications as notifs
from decafclaw.events import EventBus
from decafclaw.notification_channels.mattermost_dm import (
    _format_dm,
    _meets_priority,
    _resolve_link,
    make_mattermost_dm_adapter,
)

# -- Small helpers ------------------------------------------------------------


def _rec(**overrides) -> notifs.NotificationRecord:
    """Build a NotificationRecord with sensible defaults."""
    return notifs.NotificationRecord(
        id=overrides.get("id", "abc123"),
        timestamp=overrides.get("timestamp", "2026-04-23T12:00:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat completed"),
        priority=overrides.get("priority", "normal"),
        body=overrides.get("body", ""),
        link=overrides.get("link"),
        conv_id=overrides.get("conv_id"),
    )


def _enabled_config(config, recipient="les", min_priority="high"):
    """Turn on Mattermost DM channel in the test config."""
    config.notifications.channels.mattermost_dm.enabled = True
    config.notifications.channels.mattermost_dm.recipient_username = recipient
    config.notifications.channels.mattermost_dm.min_priority = min_priority
    return config


# -- Priority threshold -------------------------------------------------------


class TestMeetsPriority:
    def test_exact_match(self):
        assert _meets_priority("high", "high") is True

    def test_above_threshold(self):
        assert _meets_priority("high", "normal") is True

    def test_below_threshold(self):
        assert _meets_priority("low", "high") is False
        assert _meets_priority("normal", "high") is False

    def test_unknown_priority_treated_as_normal(self):
        # Defensive default: unknown strings sort as "normal" (middle of the road).
        assert _meets_priority("weird", "normal") is True
        assert _meets_priority("weird", "high") is False


# -- Link resolution ----------------------------------------------------------


class TestResolveLink:
    def test_explicit_http_link_wins(self):
        rec = _rec(link="https://example.com/x", conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local") == "https://example.com/x"

    def test_conv_id_maps_to_base_url(self):
        rec = _rec(conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local") == "http://agent.local/#conv=c-1"

    def test_base_url_trailing_slash_stripped(self):
        rec = _rec(conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local/") == "http://agent.local/#conv=c-1"

    def test_no_conv_no_link_no_base_url(self):
        assert _resolve_link(_rec(), "") is None

    def test_conv_id_without_base_url(self):
        assert _resolve_link(_rec(conv_id="c-1"), "") is None

    def test_conv_scheme_link_ignored_falls_back_to_conv_id(self):
        # `conv://...` is a web-UI scheme; the DM can't follow it verbatim.
        # Should fall through to the base_url + conv_id path.
        rec = _rec(link="conv://c-1", conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local") == "http://agent.local/#conv=c-1"


# -- Body formatting ----------------------------------------------------------


class TestFormatDM:
    def test_basic_title_and_body(self):
        dm = _format_dm(_rec(title="Hi", body="details"), "")
        assert "Hi" in dm
        assert "details" in dm
        assert "**Hi**" in dm  # bolded header

    def test_high_priority_glyph(self):
        dm = _format_dm(_rec(priority="high"), "")
        assert dm.startswith("⚠️")

    def test_normal_priority_glyph(self):
        dm = _format_dm(_rec(priority="normal"), "")
        assert dm.startswith("🔔")

    def test_includes_link_when_resolvable(self):
        dm = _format_dm(_rec(conv_id="c-1"), "http://agent.local")
        assert "http://agent.local/#conv=c-1" in dm

    def test_no_link_line_when_unresolvable(self):
        dm = _format_dm(_rec(), "")
        assert "→" not in dm

    def test_empty_body_has_only_header(self):
        dm = _format_dm(_rec(title="T", body=""), "")
        assert dm.count("\n") == 0


# -- Adapter handler end-to-end ----------------------------------------------


class TestAdapterHandler:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_dm(self, config):
        _enabled_config(config, recipient="les", min_priority="normal")
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        await handler({
            "type": "notification_created",
            "record": _rec(priority="high", title="Alert").to_dict(),
        })
        # _deliver runs in a create_task — let it flush
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        mm_client.post_direct_message.assert_awaited_once()
        username, text = mm_client.post_direct_message.await_args.args
        assert username == "les"
        assert "Alert" in text

    @pytest.mark.asyncio
    async def test_ignores_unrelated_event_types(self, config):
        _enabled_config(config)
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        await handler({"type": "tool_start", "context_id": "x"})
        await asyncio.sleep(0)
        mm_client.post_direct_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, config):
        _enabled_config(config)
        config.notifications.channels.mattermost_dm.enabled = False
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        mm_client.post_direct_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_recipient_empty(self, config):
        _enabled_config(config, recipient="")
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        mm_client.post_direct_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_by_min_priority(self, config):
        _enabled_config(config, min_priority="high")
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        # normal < high — should be filtered out
        await handler({
            "type": "notification_created",
            "record": _rec(priority="normal").to_dict(),
        })
        await asyncio.sleep(0)
        mm_client.post_direct_message.assert_not_called()

        # high >= high — should pass through
        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mm_client.post_direct_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delivery_exceptions_are_swallowed(self, config, caplog):
        _enabled_config(config, min_priority="normal")
        mm_client = AsyncMock()
        mm_client.post_direct_message.side_effect = RuntimeError("mm down")
        handler = make_mattermost_dm_adapter(config, mm_client)

        # Should not raise from the handler
        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        # Let _deliver task run and fail
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Warning was logged; no exception bubbled up
        assert any("Mattermost DM delivery failed" in r.message
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_unknown_recipient_logs_warning(self, config, caplog):
        """post_direct_message returns None when the user isn't found —
        don't let that be a silent failure.
        """
        import logging
        caplog.set_level(logging.WARNING)
        _enabled_config(config, recipient="ghost", min_priority="normal")
        mm_client = AsyncMock()
        # Simulate "user not found" — MattermostClient returns None.
        mm_client.post_direct_message.return_value = None
        handler = make_mattermost_dm_adapter(config, mm_client)

        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert any("recipient 'ghost' not found" in r.message
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_config_reread_each_event(self, config):
        """An in-process config mutation takes effect on the next event.

        The handler re-reads the in-memory config per event, so any
        programmatic change (the test mutates the dataclass directly
        here) is picked up without re-wiring the subscriber. Editing
        `config.json` on disk would still require a restart — there's
        no file watcher — but that's out of scope for this test.
        """
        _enabled_config(config, min_priority="normal")
        mm_client = AsyncMock()
        handler = make_mattermost_dm_adapter(config, mm_client)

        # First event: enabled → delivered
        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert mm_client.post_direct_message.await_count == 1

        # Flip the config; second event should be skipped
        config.notifications.channels.mattermost_dm.enabled = False
        await handler({
            "type": "notification_created",
            "record": _rec(priority="high").to_dict(),
        })
        await asyncio.sleep(0)
        assert mm_client.post_direct_message.await_count == 1  # unchanged


# -- Integration with notify() -----------------------------------------------


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_notify_triggers_adapter_via_eventbus(self, config):
        """notify() → EventBus → adapter → mm_client.post_direct_message."""
        _enabled_config(config, recipient="les", min_priority="normal")
        bus = EventBus()
        mm_client = AsyncMock()
        bus.subscribe(make_mattermost_dm_adapter(config, mm_client))

        await notifs.notify(
            config, bus, category="test", title="Ping", priority="high",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        mm_client.post_direct_message.assert_awaited_once()
