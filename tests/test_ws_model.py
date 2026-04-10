"""Tests for WebSocket model selection (replaces old effort tests)."""

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.events import EventBus
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.websocket import (
    _handle_load_history,
    _handle_set_model,
)


@pytest.fixture
def ws_state(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    # Add model configs so validation passes
    config = dataclasses.replace(config, providers={
        "vertex": ProviderConfig(type="vertex", project="test"),
    }, model_configs={
        "gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash"),
        "gemini-pro": ModelConfig(provider="vertex", model="gemini-2.5-pro"),
    }, default_model="gemini-flash")
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


class TestSetModel:
    @pytest.mark.asyncio
    async def test_set_model_records_in_archive(self, ws_state, index, conv_id):
        """set_model should persist a model message in the archive."""
        ws_send = AsyncMock()
        await _handle_set_model(ws_send, index, "testuser",
                                {"conv_id": conv_id, "model": "gemini-pro"}, ws_state)

        messages = read_archive(ws_state["config"], conv_id)
        model_msgs = [m for m in messages if m.get("role") == "model"]
        assert len(model_msgs) == 1
        assert model_msgs[0]["content"] == "gemini-pro"

    @pytest.mark.asyncio
    async def test_set_model_sends_confirmation(self, ws_state, index, conv_id):
        """set_model should send model_changed with model name."""
        ws_send = AsyncMock()
        await _handle_set_model(ws_send, index, "testuser",
                                {"conv_id": conv_id, "model": "gemini-flash"}, ws_state)

        ws_send.assert_called_once()
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "model_changed"
        assert msg["conv_id"] == conv_id
        assert msg["model"] == "gemini-flash"

    @pytest.mark.asyncio
    async def test_set_model_rejects_invalid(self, ws_state, index, conv_id):
        """set_model should reject unknown model names."""
        ws_send = AsyncMock()
        await _handle_set_model(ws_send, index, "testuser",
                                {"conv_id": conv_id, "model": "nonexistent"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"

    @pytest.mark.asyncio
    async def test_set_model_rejects_wrong_user(self, ws_state, index, conv_id):
        """set_model should reject requests from non-owner."""
        ws_send = AsyncMock()
        await _handle_set_model(ws_send, index, "otheruser",
                                {"conv_id": conv_id, "model": "gemini-pro"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"


class TestLoadHistoryModel:
    @pytest.mark.asyncio
    async def test_initial_load_includes_model(self, ws_state, index, conv_id):
        """Initial load_history should include active_model."""
        append_message(ws_state["config"], conv_id,
                       {"role": "model", "content": "gemini-pro"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "conv_history"
        assert msg["active_model"] == "gemini-pro"

    @pytest.mark.asyncio
    async def test_paginated_load_omits_model(self, ws_state, index, conv_id):
        """Paginated load_history (with before) should not include model fields."""
        append_message(ws_state["config"], conv_id,
                       {"role": "model", "content": "gemini-pro"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id, "before": "9999"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert "active_model" not in msg

    @pytest.mark.asyncio
    async def test_no_model_when_none_set(self, ws_state, index, conv_id):
        """Should not include active_model when no model message exists."""
        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert "active_model" not in msg

    @pytest.mark.asyncio
    async def test_model_messages_filtered_from_history(self, ws_state, index, conv_id):
        """Model metadata messages should not appear in the messages list."""
        config = ws_state["config"]
        append_message(config, conv_id,
                       {"role": "user", "content": "hello"})
        append_message(config, conv_id,
                       {"role": "model", "content": "gemini-pro"})
        append_message(config, conv_id,
                       {"role": "assistant", "content": "hi"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        roles = [m["role"] for m in msg["messages"]]
        assert "model" not in roles
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_model_restored_after_set(self, ws_state, index, conv_id):
        """Model set via WebSocket should be visible in subsequent load_history."""
        ws_send = AsyncMock()

        # Set model
        await _handle_set_model(ws_send, index, "testuser",
                                {"conv_id": conv_id, "model": "gemini-pro"}, ws_state)

        # Load history
        ws_send.reset_mock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": conv_id}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["active_model"] == "gemini-pro"
