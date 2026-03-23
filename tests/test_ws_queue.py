"""Tests for WebSocket message queuing during agent turns."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import _handle_send, _start_agent_turn


@pytest.fixture
def ws_state(config):
    """Minimal WebSocket handler state."""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return {
        "agent_tasks": set(),
        "cancel_events": {},
        "busy_convs": set(),
        "pending_msgs": {},
        "config": config,
        "event_bus": EventBus(),
        "app_ctx": MagicMock(config=config, event_bus=EventBus()),
        "websocket": MagicMock(),
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
    async def test_queues_when_busy(self, ws_state, conv_id, index):
        """New messages should queue when a turn is in progress (queue mode)."""
        ws_state["config"].agent.turn_on_new_message = "queue"
        ws_send = AsyncMock()

        # Mark conversation as busy
        ws_state["busy_convs"].add(conv_id)
        ws_state["cancel_events"][conv_id] = asyncio.Event()

        await _handle_send(ws_send, index, "testuser",
                           {"conv_id": conv_id, "text": "queued msg"}, ws_state)

        queued = ws_state["pending_msgs"].get(conv_id, [])
        assert len(queued) == 1
        assert queued[0]["text"] == "queued msg"
        assert queued[0]["command_skill"] is None

    @pytest.mark.asyncio
    async def test_does_not_cancel_in_queue_mode(self, ws_state, conv_id, index):
        """Queue mode should not set the cancel event."""
        ws_state["config"].agent.turn_on_new_message = "queue"
        ws_send = AsyncMock()

        cancel_event = asyncio.Event()
        ws_state["busy_convs"].add(conv_id)
        ws_state["cancel_events"][conv_id] = cancel_event

        await _handle_send(ws_send, index, "testuser",
                           {"conv_id": conv_id, "text": "queued msg"}, ws_state)

        assert not cancel_event.is_set()


class TestCancelMode:
    @pytest.mark.asyncio
    async def test_cancels_when_busy(self, ws_state, conv_id, index):
        """Cancel mode should cancel the current turn."""
        ws_state["config"].agent.turn_on_new_message = "cancel"
        ws_send = AsyncMock()

        cancel_event = asyncio.Event()
        ws_state["busy_convs"].add(conv_id)
        ws_state["cancel_events"][conv_id] = cancel_event

        await _handle_send(ws_send, index, "testuser",
                           {"conv_id": conv_id, "text": "new msg"}, ws_state)

        assert cancel_event.is_set()
        # Message should still be queued for processing after cancel
        queued = ws_state["pending_msgs"].get(conv_id, [])
        assert len(queued) == 1


class TestQueueDrain:
    @pytest.mark.asyncio
    async def test_drains_queue_after_turn(self, ws_state, conv_id, index):
        """Queued messages should be processed after the current turn completes."""
        ws_send = AsyncMock()
        turn_started = asyncio.Event()
        turn_texts = []

        async def fake_agent_turn(*args, **kwargs):
            # Record what text was passed
            turn_texts.append(args[7])  # text is the 8th positional arg
            turn_started.set()

        with patch("decafclaw.web.websocket._run_agent_turn", side_effect=fake_agent_turn):
            # Start a turn
            _start_agent_turn(ws_state, index, conv_id, "testuser", "first msg", ws_send)
            await turn_started.wait()

            # Queue a message
            ws_state["pending_msgs"][conv_id] = [
                {"text": "second msg", "command_skill": None}
            ]

            # Let the done callback fire
            turn_started.clear()
            task = list(ws_state["agent_tasks"])[0]
            await task

            # The done callback should have started a new turn
            await asyncio.sleep(0.01)  # let the new task start
            assert "first msg" in turn_texts
            if len(turn_texts) > 1:
                assert "second msg" in turn_texts[1]

    @pytest.mark.asyncio
    async def test_skips_drain_when_closing(self, ws_state, conv_id, index):
        """Queue should not drain when the connection is closing."""
        ws_send = AsyncMock()

        async def fake_agent_turn(*args, **kwargs):
            pass

        with patch("decafclaw.web.websocket._run_agent_turn", side_effect=fake_agent_turn):
            _start_agent_turn(ws_state, index, conv_id, "testuser", "msg", ws_send)

            # Queue a message and mark closing
            ws_state["pending_msgs"][conv_id] = [
                {"text": "should not run", "command_skill": None}
            ]
            ws_state["closing"] = True

            # Let the task complete
            task = list(ws_state["agent_tasks"])[0]
            await task
            await asyncio.sleep(0.01)

            # Queue should have been cleared, no new task started
            assert conv_id not in ws_state["pending_msgs"]
            assert len(ws_state["agent_tasks"]) == 0
