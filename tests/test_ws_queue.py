"""Tests for WebSocket message handling via ConversationManager.

The queueing and turn lifecycle are now owned by the ConversationManager
(tested in test_conversation_manager.py). These tests verify the WebSocket
handler correctly delegates to the manager.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import _handle_cancel_turn, _handle_send


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


@pytest.fixture
def ws_state(config, manager):
    """Minimal WebSocket handler state with manager."""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return {
        "config": config,
        "event_bus": manager.event_bus,
        "app_ctx": MagicMock(config=config, event_bus=manager.event_bus),
        "websocket": MagicMock(),
        "ws_send": AsyncMock(),
        "manager": manager,
    }


@pytest.fixture
def conv_id(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    index = ConversationIndex(config)
    conv = index.create("testuser", title="Test")
    return conv.conv_id


@pytest.fixture
def index(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return ConversationIndex(config)


class TestQueueMode:
    @pytest.mark.asyncio
    async def test_queues_when_busy(self, ws_state, conv_id, index, manager):
        """New messages should queue in the manager when a turn is in progress."""
        ws_send = AsyncMock()

        # Mark conversation as busy in the manager
        state = manager._get_or_create(conv_id)
        state.busy = True

        await _handle_send(ws_send, index, "testuser",
                           {"conv_id": conv_id, "text": "queued msg"}, ws_state)

        assert len(state.pending_messages) == 1
        assert state.pending_messages[0]["text"] == "queued msg"

    @pytest.mark.asyncio
    async def test_does_not_cancel_in_queue_mode(self, ws_state, conv_id, index, manager):
        """Queue mode should not cancel — messages queue in the manager."""
        ws_send = AsyncMock()

        cancel_event = asyncio.Event()
        state = manager._get_or_create(conv_id)
        state.busy = True
        state.cancel_event = cancel_event

        await _handle_send(ws_send, index, "testuser",
                           {"conv_id": conv_id, "text": "queued msg"}, ws_state)

        assert not cancel_event.is_set()


class TestCancelMode:
    @pytest.mark.asyncio
    async def test_cancels_when_requested(self, ws_state, conv_id, index, manager):
        """Cancel turn should cancel via the manager."""
        ws_send = AsyncMock()

        cancel_event = asyncio.Event()
        state = manager._get_or_create(conv_id)
        state.cancel_event = cancel_event

        async def fake_task():
            await asyncio.sleep(10)

        state.agent_task = asyncio.create_task(fake_task())

        await _handle_cancel_turn(ws_send, index, "testuser",
                                  {"conv_id": conv_id}, ws_state)

        assert cancel_event.is_set()
        await asyncio.sleep(0.05)
        assert state.agent_task.cancelled()


class TestQueueDrain:
    @pytest.mark.asyncio
    async def test_drains_queue_after_turn(self, manager, conv_id):
        """Queued messages drain via the manager after turn completes."""
        state = manager._get_or_create(conv_id)

        # Simulate a completed turn with queued messages
        state.pending_messages = [
            {"text": "queued msg", "user_id": "testuser",
             "context_setup": None, "archive_text": "",
             "attachments": None, "command_ctx": None, "wiki_page": None},
        ]

        # Drain should process the queued message
        # (will try to start a turn, which will fail without full agent setup,
        # but we can verify the queue was consumed)
        try:
            await manager._drain_pending(state)
        except Exception:
            pass  # Expected — no real agent to run

        assert len(state.pending_messages) == 0
