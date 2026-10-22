"""Tests for the Mattermost transport's compaction_end notice.

Drives `MattermostClient._ensure_subscribed`'s `on_manager_event`
closure directly: capture the callback it registers with the manager,
feed it compaction_start/compaction_end, and pin the text the post is
edited to. #1053 — these pin the full user-visible string (emoji prefix
+ shared notice), so a transport that drifts without using
`compaction_notice` surfaces here.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
async def test_mattermost_delivers_wake_delivery_event():
    """MattermostClient sends fallback wake delivery when no conversation subscriber is live (#1119 review)."""
    from decafclaw.events import EventBus

    client = _make_client()
    event_bus = EventBus()

    async def on_wake_delivery(event):
        if event.get("type") != "wake_delivery":
            return
        channel_id = event.get("channel_id")
        if not channel_id:
            return
        text = event.get("text", "")
        root_id = event.get("thread_id") or None
        if text:
            await client.send(channel_id, text, root_id=root_id)

    sub_id = event_bus.subscribe(on_wake_delivery)

    await event_bus.publish(
        {
            "type": "wake_delivery",
            "channel_id": "ch-wake-99",
            "thread_id": "root-wake-11",
            "text": "Scheduled task finished!",
        }
    )

    client.send.assert_awaited_once_with("ch-wake-99", "Scheduled task finished!", root_id="root-wake-11")
    event_bus.unsubscribe(sub_id)
