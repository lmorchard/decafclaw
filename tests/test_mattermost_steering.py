"""Mattermost send path forwards the conversation's busy state as `steering` (#1442).

The manager owns the final decision: a stale busy read just starts a normal
turn, because `enqueue_turn` only sets `steer_event` while still busy.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.events import EventBus
from decafclaw.mattermost import MattermostClient


def _make_client():
    """Bare client with the transport calls `_process_conversation` makes mocked."""
    client = MattermostClient.__new__(MattermostClient)
    client.cooldown_ms = 0
    client.send_placeholder = AsyncMock(return_value=None)
    client.send_typing = AsyncMock()
    client._download_attachments = AsyncMock(return_value=None)
    client._ensure_subscribed = MagicMock()
    return client


def _msgs():
    return [
        {
            "text": "hello",
            "post_id": "post1",
            "channel_id": "ch1",
            "channel_type": "D",
            "root_id": "",
            "sender_name": "user",
            "mentioned": False,
        }
    ]


async def _run(client, config, manager):
    app_ctx = SimpleNamespace(config=config, event_bus=EventBus())
    await client._process_conversation(
        "conv1",
        "ch1",
        _msgs(),
        app_ctx,
        {},
        manager,
    )


@pytest.mark.asyncio
async def test_busy_conversation_passes_steering_true(config):
    client = _make_client()
    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=True)
    manager.send_message = AsyncMock()

    await _run(client, config, manager)

    manager.send_message.assert_awaited_once()
    assert manager.send_message.await_args.kwargs["steering"] is True


@pytest.mark.asyncio
async def test_idle_conversation_passes_steering_false(config):
    client = _make_client()
    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=False)
    manager.send_message = AsyncMock()

    await _run(client, config, manager)

    manager.send_message.assert_awaited_once()
    assert manager.send_message.await_args.kwargs["steering"] is False
