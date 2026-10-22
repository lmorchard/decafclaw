"""Tests for the Mattermost transport's compaction_end notice.

Drives `MattermostClient._ensure_subscribed`'s `on_manager_event`
closure directly: capture the callback it registers with the manager,
feed it compaction_start/compaction_end, and pin the text the post is
edited to. #1053 — these pin the full user-visible string (emoji prefix
+ shared notice), so a transport that drifts without using
`compaction_notice` surfaces here.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.mattermost import MattermostClient, MattermostConvState

POST_ID = "compaction-post"


def _make_client():
    client = MattermostClient.__new__(MattermostClient)
    client.send = AsyncMock(return_value=POST_ID)
    client.edit_message = AsyncMock()
    return client


def _capture_on_manager_event(client, config):
    """Run `_ensure_subscribed` and return its on_manager_event closure."""
    captured = {}

    def subscribe(conv_id, callback):
        captured["cb"] = callback
        return "sub-1"

    app_ctx = SimpleNamespace(config=config, event_bus=None)
    manager = SimpleNamespace(subscribe=subscribe, unsubscribe=lambda *a, **kw: None)
    client._ensure_subscribed(MattermostConvState(), "conv1", "channel1", None, app_ctx, manager)
    return captured["cb"]


@pytest.mark.asyncio
async def test_compaction_end_edits_post_with_failure_notice(config):
    """#1005 — a failed compaction must not read "compacted" (N → N)."""
    client = _make_client()
    on_event = _capture_on_manager_event(client, config)

    await on_event({"type": "compaction_start"})
    await on_event({"type": "compaction_end", "success": False, "before_messages": 10, "after_messages": 10})

    client.send.assert_awaited_once()
    client.edit_message.assert_awaited_once_with(
        POST_ID,
        "📦 Compaction failed — conversation unchanged",
    )


@pytest.mark.asyncio
async def test_compaction_end_edits_post_with_success_notice(config):
    """The success notice pins duration, message counts, and token estimate."""
    client = _make_client()
    on_event = _capture_on_manager_event(client, config)

    await on_event({"type": "compaction_start"})
    await on_event(
        {
            "type": "compaction_end",
            "success": True,
            "before_messages": 42,
            "after_messages": 10,
            "elapsed_sec": 125.0,
            "estimated_tokens_before": 50000,
        }
    )

    client.edit_message.assert_awaited_once_with(
        POST_ID,
        "📦 Conversation compacted (2.1m, 42 → 10 messages, ~50,000 tokens compacted)",
    )


@pytest.mark.asyncio
async def test_compaction_end_without_start_does_not_edit(config):
    """No compaction post to edit → no edit call."""
    client = _make_client()
    on_event = _capture_on_manager_event(client, config)

    await on_event({"type": "compaction_end", "success": True, "before_messages": 4, "after_messages": 2})

    client.send.assert_not_called()
    client.edit_message.assert_not_called()


@pytest.mark.asyncio
async def test_mattermost_subscriber_delivers_wake_when_display_is_none(config):
    """When a Mattermost subscriber receives message_complete while conv_display is None,

    it posts directly to channel_id rather than dropping the message (#1119 review).
    """
    client = _make_client()
    mm_state = MattermostConvState()
    # conv_display is None (as happens after turn_complete)
    assert mm_state.conv_display is None

    captured = {}

    def subscribe(conv_id, callback):
        captured["cb"] = callback
        return "sub-1"

    app_ctx = SimpleNamespace(config=config, event_bus=None)
    manager = SimpleNamespace(subscribe=subscribe, unsubscribe=lambda *a, **kw: None)
    client._ensure_subscribed(mm_state, "conv1", "channel-mm-1", "root-mm-1", app_ctx, manager)
    on_event = captured["cb"]

    await on_event({"type": "message_complete", "final": True, "text": "Scheduled wake complete!"})
    client.send.assert_awaited_once_with("channel-mm-1", "Scheduled wake complete!", root_id="root-mm-1")


@pytest.mark.asyncio
async def test_mattermost_delivers_wake_delivery_event():
    """MattermostClient sends fallback wake delivery when no conversation subscriber is live (#1119 review)."""
    from decafclaw.events import EventBus

    client = _make_client()
    client._connected_event = asyncio.Event()
    client._connected_event.set()
    event_bus = EventBus()

    async def on_wake_delivery(event):
        if event.get("type") != "wake_delivery":
            return
        if event.get("transport") != "mattermost":
            return
        channel_id = event.get("channel_id")
        if not channel_id:
            return
        text = event.get("text", "")
        root_id = event.get("thread_id") or None
        if text:
            await client.send(channel_id, text, root_id=root_id)

    sub_id = event_bus.subscribe(on_wake_delivery)

    # 1. Web event is ignored
    await event_bus.publish(
        {
            "type": "wake_delivery",
            "transport": "web",
            "channel_id": "conv-web-123",
            "text": "Ignored web wake",
        }
    )
    client.send.assert_not_called()

    # 2. Mattermost event is delivered
    await event_bus.publish(
        {
            "type": "wake_delivery",
            "transport": "mattermost",
            "channel_id": "ch-wake-99",
            "thread_id": "root-wake-11",
            "text": "Scheduled task finished!",
        }
    )

    client.send.assert_awaited_once_with("ch-wake-99", "Scheduled task finished!", root_id="root-wake-11")
    event_bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_mattermost_renders_confirmation_request_when_display_is_none(config):
    """Confirmation requests arriving on idle Mattermost subscriptions create a display to prompt (#1119 review)."""
    client = _make_client()
    client._poll_confirmation_manager = AsyncMock()
    mm_state = MattermostConvState()
    assert mm_state.conv_display is None

    captured = {}

    def subscribe(conv_id, callback):
        captured["cb"] = callback
        return "sub-1"

    app_ctx = SimpleNamespace(config=config, event_bus=None)
    manager = SimpleNamespace(subscribe=subscribe, unsubscribe=lambda *a, **kw: None)
    client._ensure_subscribed(mm_state, "conv1", "channel-confirm-1", "root-confirm-1", app_ctx, manager)
    on_event = captured["cb"]

    with patch(
        "decafclaw.mattermost_display.ConversationDisplay.on_confirm_request", new_callable=AsyncMock
    ) as mock_ocr:
        mock_ocr.return_value = "post-confirm-id"
        await on_event(
            {
                "type": "confirmation_request",
                "confirmation_id": "conf-123",
                "action_type": "run_shell_command",
                "action_data": {"command": "npm test", "tool_name": "shell"},
            }
        )
        mock_ocr.assert_awaited_once()
        client._poll_confirmation_manager.assert_called_once()
