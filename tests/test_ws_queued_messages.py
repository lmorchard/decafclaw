"""Tests for queued user messages in WebSocket conversation history (#972)."""

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.events import EventBus
from decafclaw.inbox import _append_inbox, _read_inbox
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import _handle_load_history


@pytest.fixture
def ws_state(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config = dataclasses.replace(
        config,
        providers={"vertex": ProviderConfig(type="vertex", project="test")},
        model_configs={"gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash")},
        default_model="gemini-flash",
    )
    return {
        "config": config,
        "event_bus": EventBus(),
        "app_ctx": MagicMock(config=config, event_bus=EventBus()),
        "websocket": MagicMock(),
    }


@pytest.fixture
def index(ws_state):
    return ConversationIndex(ws_state["config"])


@pytest.fixture
def conv_id(index):
    conv = index.create("testuser", title="Test")
    return conv.conv_id


class TestQueuedMessagesInHistory:
    @pytest.mark.asyncio
    async def test_queued_inbox_message_included_in_history(self, ws_state, index, conv_id):
        """When an agent is busy and a user message is queued in inbox, load_history includes it."""
        config = ws_state["config"]
        # Add existing conversation messages to archive
        append_message(config, conv_id, {"role": "user", "content": "Initial prompt"})
        append_message(config, conv_id, {"role": "assistant", "content": "Working on it..."})

        # Enqueue a message into inbox (as manager does when busy)
        _append_inbox(
            config,
            conv_id,
            {
                "turn_id": "t1",
                "kind": "user",
                "text": "Queued message while busy",
                "user_id": "testuser",
                "timestamp": "2026-10-07T12:00:00Z",
                "attachments": [{"filename": "doc.pdf", "path": "uploads/doc.pdf"}],
            },
        )

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser", {"conv_id": conv_id}, ws_state)

        ws_send.assert_called_once()
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "conv_history"

        # The queued message must be in the history
        contents = [m.get("content") for m in msg["messages"]]
        assert "Queued message while busy" in contents
        queued_msg = next(m for m in msg["messages"] if m.get("content") == "Queued message while busy")
        assert queued_msg["role"] == "user"
        assert queued_msg["attachments"] == [{"filename": "doc.pdf", "path": "uploads/doc.pdf"}]
        assert queued_msg["timestamp"] == "2026-10-07T12:00:00Z"

    @pytest.mark.asyncio
    async def test_queued_inbox_message_sets_turn_active(self, ws_state, index, conv_id):
        """When an inbox has queued messages, turn_active should be set."""
        config = ws_state["config"]
        _append_inbox(
            config,
            conv_id,
            {
                "turn_id": "t1",
                "kind": "user",
                "text": "Queued message",
                "user_id": "testuser",
            },
        )
        manager = MagicMock()
        conv_state = MagicMock()
        conv_state.busy = False  # Not busy right now, but has inbox pending
        manager.get_state.return_value = conv_state
        ws_state["manager"] = manager

        ws_send = AsyncMock()
        ws_state["ws_send"] = ws_send
        await _handle_load_history(ws_send, index, "testuser", {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg.get("turn_active") is True

    @pytest.mark.asyncio
    async def test_paginated_history_excludes_queued_inbox_messages(self, ws_state, index, conv_id):
        """Paginated history requests (before timestamp) must NOT include pending inbox messages."""
        config = ws_state["config"]
        append_message(config, conv_id, {"role": "user", "content": "Old message", "timestamp": "2026-01-01T00:00:00Z"})
        _append_inbox(
            config,
            conv_id,
            {
                "turn_id": "t1",
                "kind": "user",
                "text": "Future queued message",
                "user_id": "testuser",
            },
        )

        ws_send = AsyncMock()
        await _handle_load_history(
            ws_send, index, "testuser", {"conv_id": conv_id, "before": "2026-01-02T00:00:00Z"}, ws_state
        )

        msg = ws_send.call_args[0][0]
        contents = [m.get("content") for m in msg["messages"]]
        assert "Future queued message" not in contents
