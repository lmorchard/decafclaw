"""Tests for WebSocket effort level indicator and picker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import (
    _handle_load_history,
    _handle_set_effort,
)


@pytest.fixture
def ws_state(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return {
        "config": config,
        "event_bus": EventBus(),
        "app_ctx": MagicMock(config=config, event_bus=EventBus()),
        "websocket": MagicMock(),
    }


@pytest.fixture
def index(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return ConversationIndex(config)


@pytest.fixture
def conv_id(index):
    conv = index.create("testuser", title="Test")
    return conv.conv_id


class TestSetEffort:
    @pytest.mark.asyncio
    async def test_set_effort_records_in_archive(self, ws_state, index, conv_id):
        """set_effort should persist an effort message in the archive."""
        ws_send = AsyncMock()
        await _handle_set_effort(ws_send, index, "testuser",
                                 {"conv_id": conv_id, "level": "strong"}, ws_state)

        messages = read_archive(ws_state["config"], conv_id)
        effort_msgs = [m for m in messages if m.get("role") == "effort"]
        assert len(effort_msgs) == 1
        assert effort_msgs[0]["content"] == "strong"

    @pytest.mark.asyncio
    async def test_set_effort_sends_confirmation(self, ws_state, index, conv_id):
        """set_effort should send effort_changed with level and model."""
        ws_send = AsyncMock()
        await _handle_set_effort(ws_send, index, "testuser",
                                 {"conv_id": conv_id, "level": "fast"}, ws_state)

        ws_send.assert_called_once()
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "effort_changed"
        assert msg["conv_id"] == conv_id
        assert msg["level"] == "fast"
        assert "model" in msg

    @pytest.mark.asyncio
    async def test_set_effort_rejects_invalid_level(self, ws_state, index, conv_id):
        """set_effort should reject unknown effort levels."""
        ws_send = AsyncMock()
        await _handle_set_effort(ws_send, index, "testuser",
                                 {"conv_id": conv_id, "level": "turbo"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"

    @pytest.mark.asyncio
    async def test_set_effort_rejects_wrong_user(self, ws_state, index, conv_id):
        """set_effort should reject requests from non-owner."""
        ws_send = AsyncMock()
        await _handle_set_effort(ws_send, index, "otheruser",
                                 {"conv_id": conv_id, "level": "strong"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"


class TestLoadHistoryEffort:
    @pytest.mark.asyncio
    async def test_initial_load_includes_effort(self, ws_state, index, conv_id):
        """Initial load_history should include current_effort and effort_model."""
        # Set effort in archive
        append_message(ws_state["config"], conv_id,
                       {"role": "effort", "content": "strong"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "conv_history"
        assert msg["current_effort"] == "strong"
        assert "effort_model" in msg

    @pytest.mark.asyncio
    async def test_paginated_load_omits_effort(self, ws_state, index, conv_id):
        """Paginated load_history (with before) should not include effort fields."""
        append_message(ws_state["config"], conv_id,
                       {"role": "effort", "content": "strong"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id, "before": "9999"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert "current_effort" not in msg
        assert "effort_model" not in msg

    @pytest.mark.asyncio
    async def test_default_effort_when_none_set(self, ws_state, index, conv_id):
        """Should return 'default' effort when no effort message exists."""
        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["current_effort"] == "default"

    @pytest.mark.asyncio
    async def test_effort_messages_filtered_from_history(self, ws_state, index, conv_id):
        """Effort metadata messages should not appear in the messages list."""
        config = ws_state["config"]
        append_message(config, conv_id,
                       {"role": "user", "content": "hello"})
        append_message(config, conv_id,
                       {"role": "effort", "content": "strong"})
        append_message(config, conv_id,
                       {"role": "assistant", "content": "hi"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        roles = [m["role"] for m in msg["messages"]]
        assert "effort" not in roles
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_effort_restored_after_set(self, ws_state, index, conv_id):
        """Effort set via WebSocket should be visible in subsequent load_history."""
        ws_send = AsyncMock()

        # Set effort
        await _handle_set_effort(ws_send, index, "testuser",
                                 {"conv_id": conv_id, "level": "strong"}, ws_state)

        # Load history
        ws_send.reset_mock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["current_effort"] == "strong"


class TestCreateConvEffort:
    """Tests for effort level on conversation creation (now via REST)."""

    # These tests are in test_web_conversations.py (test_create_conv_with_effort)
    # since conversation creation moved from WebSocket to REST.
    pass
